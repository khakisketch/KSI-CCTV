"""
안전 관리 파이프라인

캡처 → 탐지 → 알람 연결
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from src.capture.rtsp_capture import CameraManager
from src.detection.yolo_detector import Detection, YOLODetector

if TYPE_CHECKING:
    from src.detection.model_registry import MultiModelRegistry  # noqa: F401
    from src.policy.event_policy_engine import EventPolicyEngine  # noqa: F401

logger = logging.getLogger(__name__)

@dataclass
class SafetyEvent:
    """안전 이벤트"""
    event_id: str
    camera_id: str
    event_type: str  # e.g. "no_helmet", "fire", "fall", "person"
    timestamp: datetime
    confidence: float
    detections: list[Detection] = field(default_factory=list)
    image: np.ndarray | None = None
    description: str = ""
    # VLM analysis fields
    vlm_risk_level: str = ""
    vlm_confidence: float = 0.0
    vlm_inference_ms: float = 0.0
    # Policy engine fields (Phase 4)
    alert_level: str = "low"
    cooldown_sec: float = 30.0
    vlm_needed: bool = False
    source_models: list[str] = field(default_factory=list)
    rule_name: str = ""

    def to_dict(self):
        return {
            "event_id": self.event_id,
            "camera_id": self.camera_id,
            "event_type": self.event_type,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "detections": [d.to_dict() for d in self.detections],
            "description": self.description,
            "vlm_risk_level": self.vlm_risk_level,
            "vlm_confidence": self.vlm_confidence,
            "vlm_inference_ms": self.vlm_inference_ms,
            # Policy engine fields (Phase 4)
            "alert_level": self.alert_level,
            "cooldown_sec": self.cooldown_sec,
            "vlm_needed": self.vlm_needed,
            "source_models": list(self.source_models),
            "rule_name": self.rule_name,
        }


class SafetyPipeline:
    """
    안전 관리 파이프라인
    
    카메라 캡처 → YOLO 탐지 → 이벤트 생성 → 알람 발송
    """

    def __init__(
        self,
        detector: YOLODetector | None = None,
        skip_frames: int = 5,
        alert_cooldown: float = 30.0,
        target_classes: list[str] | None = None,
        vlm_trigger_classes: list[str] | None = None,
        vlm_cooldown: float | None = None,
        # Phase 4 policy-based injection (optional)
        model_registry=None,  # MultiModelRegistry | None
        policy_engine=None,   # EventPolicyEngine | None
        # Phase 8 M5.3: Dynamic frame skip
        dynamic_skip: bool = False,
        motion_threshold: float = 0.02,
        static_skip_multiplier: int = 4,
        # Phase 9: VLM timestamp cache
        vlm_cache_ttl_sec: float = 30.0,
        vlm_cache_bypass_levels: list[str] | None = None,
    ):
        self.detector = detector
        self.skip_frames = skip_frames
        self.alert_cooldown = alert_cooldown
        self.target_classes = set(target_classes or ["person", "fire", "smoke", "fall"])
        self.vlm_trigger_classes = set(vlm_trigger_classes or ["person"])
        self.vlm_cooldown = float(vlm_cooldown if vlm_cooldown is not None else alert_cooldown)

        # Phase 4: policy-based pipeline mode
        self.model_registry = model_registry
        self.policy_engine = policy_engine
        self.use_policy_mode = (model_registry is not None and policy_engine is not None)

        # Camera profile for policy evaluation (set via set_camera_profile)
        self._camera_profiles: dict[str, dict] = {}

        self.camera_manager = CameraManager()
        self.is_running = False
        self.threads: dict[str, threading.Thread] = {}

        # 알람 콜백
        self.alert_callbacks: list[Callable[[SafetyEvent], None]] = []
        self.analysis_callbacks: list[Callable[[SafetyEvent], None]] = []

        # 프레임 카운터
        self.frame_counters: dict[str, int] = {}

        # 알람 쿨다운 관리
        self.last_alerts: dict[str, float] = {}
        self.last_vlm_observations: dict[str, float] = {}

        # 이벤트 히스토리
        self.event_history: list[SafetyEvent] = []
        self.max_history = 1000
        self.latest_detections: dict[str, list[Detection]] = {}
        self.latest_result_lock = threading.Lock()

        # 공유 상태(event_history, last_alerts, last_vlm_observations, _vlm_cache 등)를
        # 카메라별 스레드 간 동시 접근으로부터 보호한다.
        self._state_lock = threading.Lock()
        # 메모리 보호: 히스토리 메타데이터는 max_history까지 유지하되, 원본 프레임
        # 이미지는 최근 _history_image_keep건만 보존(이후 해제)해 누적 메모리를 제한한다.
        self._history_image_keep = 50

        # Phase 8 M5.3: Dynamic frame skip
        self.dynamic_skip = dynamic_skip
        self.motion_threshold = motion_threshold
        self.static_skip_multiplier = static_skip_multiplier
        self._last_frame_gray: dict[str, np.ndarray] = {}
        self._motion_detected: dict[str, bool] = {}

        # Phase 9: VLM timestamp cache (camera_id, event_type) → last_analyzed_ts
        self.vlm_cache_ttl_sec = float(vlm_cache_ttl_sec)
        self.vlm_cache_bypass_levels = set(
            vlm_cache_bypass_levels or ["critical", "high"]
        )
        self._vlm_cache: dict[tuple, float] = {}
        self._vlm_cache_stats = {
            "hits": 0,
            "misses": 0,
            "bypasses": 0,
        }

        # VLM 분석
        self.vlm_provider = None
        self.context_builder = None
        self._vlm_executor: ThreadPoolExecutor | None = None

        logger.info("SafetyPipeline 초기화 완료")

    def enable_vlm(self, provider, prompt_template_path: str):
        """VLM 분석 활성화"""
        from src.vlm.context_builder import ContextBuilder
        from src.vlm.ollama_provider import OllamaProvider
        from src.vlm.prompt_selector import PromptSelector

        if not isinstance(provider, OllamaProvider):
            logger.error("지원하지 않는 VLM 제공자입니다")
            return

        self.vlm_provider = provider
        # 기본 컨텍스트 빌더 (하위 호환성)
        self.context_builder = ContextBuilder(prompt_template_path)
        # 프롬프트 선택기 초기화
        self.prompt_selector = PromptSelector()
        self._vlm_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="vlm")
        available = provider.is_available()
        logger.info(f"VLM 활성화 완료 (provider={provider.model}, available={available})")

    def set_camera_profile(self, camera_id: str, profile: dict):
        """카메라별 정책 프로필 설정 (Phase 4)"""
        self._camera_profiles[camera_id] = profile

    def _analyze_with_vlm(self, event: SafetyEvent):
        """별도 스레드에서 VLM 분석 수행"""
        from src.vlm.context_builder import ContextBuilder

        if self.vlm_provider is None or event.image is None:
            return

        try:
            # BGR → JPEG bytes
            _, buf = cv2.imencode(".jpg", event.image)
            image_bytes = buf.tobytes()

            # 카메라의 detection_type에 따라 프롬프트 자동 선택
            camera_config = self._camera_profiles.get(event.camera_id, {})
            detection_type = camera_config.get("detection_type", "default") if isinstance(camera_config, dict) else "default"

            # 컨텍스트 구성
            det_summary = ContextBuilder.format_detections(event.detections)
            recent_events = ContextBuilder.format_events(self.event_history, max_count=5)

            # 프롬프트 선택기 사용 (있으면), 없으면 기본 빌더
            if hasattr(self, 'prompt_selector') and self.prompt_selector is not None:
                prompt = self.prompt_selector.build_prompt(detection_type, det_summary, recent_events)
            else:
                prompt = self.context_builder.build(det_summary, recent_events)

            result = self.vlm_provider.analyze(image_bytes, prompt)

            event.description = result.description
            event.vlm_risk_level = result.risk_level
            event.vlm_confidence = result.confidence
            event.vlm_inference_ms = result.inference_time_ms

            # VLM 결과의 risk_level이 event의 alert_level보다 높으면 업데이트
            risk_priority = {"low": 0, "medium": 1, "high": 2, "critical": 3}
            if risk_priority.get(result.risk_level, 0) > risk_priority.get(event.alert_level, 0):
                event.alert_level = result.risk_level

            # 파싱 실패 시 원본 응답 저장
            if hasattr(result, 'parse_success') and not result.parse_success:
                logger.warning(
                    f"[{event.camera_id}] VLM JSON 파싱 실패. 원본 응답 일부: "
                    f"{(result.raw_response or '')[:200]}"
                )

            logger.info(
                f"[{event.camera_id}] VLM 분석: {result.description[:80]} "
                f"({result.inference_time_ms:.0f}ms, risk={result.risk_level})"
            )
        except Exception as e:
            logger.error(f"VLM 분석 실패 [{event.camera_id}]: {e}")
            event.description = "VLM 분석 불가"

    def get_vlm_cache_stats(self) -> dict[str, Any]:
        """VLM cache 통계 반환."""
        with self._state_lock:
            return dict(self._vlm_cache_stats)

    def reset_vlm_cache(self) -> None:
        """VLM cache와 통계 초기화 (공개 메서드)."""
        with self._state_lock:
            self._vlm_cache.clear()
            for k in self._vlm_cache_stats:
                self._vlm_cache_stats[k] = 0
        logger.info("VLM cache 및 통계 초기화됨")

    def _record_event(self, event: SafetyEvent) -> None:
        """이벤트를 히스토리에 스레드-안전하게 기록하고 오래된 이미지를 해제한다.

        - 카메라 스레드 간 동시 append/pop으로 인한 race를 _state_lock으로 방지.
        - 메타데이터는 max_history까지 유지하되, 원본 프레임 이미지는 최근
          _history_image_keep건만 보존(이후 None)해 누적 메모리(1080p×수백건 → 수 GB)를 제한한다.
        """
        with self._state_lock:
            self.event_history.append(event)
            if len(self.event_history) > self.max_history:
                self.event_history.pop(0)
            if len(self.event_history) > self._history_image_keep:
                stale = self.event_history[-(self._history_image_keep + 1)]
                stale.image = None

    def _should_submit_vlm(self, event: SafetyEvent) -> bool:
        """VLM TTL cache에 따라 분석 제출 여부를 결정한다.

        `critical/high` 등 bypass level은 안전 이벤트 설명 누락을 피하기 위해
        cache를 무시하고 항상 제출한다. 그 외 이벤트는 `(camera_id, event_type)`
        기준 TTL 내 중복 제출을 막는다.
        """
        alert_level = (event.alert_level or "low").strip().lower()
        with self._state_lock:
            if alert_level in self.vlm_cache_bypass_levels:
                self._vlm_cache_stats["bypasses"] += 1
                return True

            cache_key = (event.camera_id, event.event_type)
            now = time.time()
            last_ts = self._vlm_cache.get(cache_key)
            if last_ts is not None and now - last_ts < self.vlm_cache_ttl_sec:
                self._vlm_cache_stats["hits"] += 1
                return False

            self._vlm_cache[cache_key] = now
            self._vlm_cache_stats["misses"] += 1
            return True

    def _submit_vlm_analysis(self, event: SafetyEvent) -> bool:
        """VLM 분석을 cache 정책에 따라 제출한다."""
        if self._vlm_executor is None or event.image is None:
            return False
        if not self._should_submit_vlm(event):
            logger.debug(
                "[%s] VLM cache hit: event_type=%s alert_level=%s",
                event.camera_id,
                event.event_type,
                event.alert_level,
            )
            return False
        try:
            self._vlm_executor.submit(self._analyze_with_vlm, event)
            return True
        except Exception as e:
            logger.error(f"VLM 제출 실패 [{event.camera_id}]: {e}")
            return False

    def stop(self):
        """파이프라인 중지"""
        self.is_running = False
        self.camera_manager.stop_all()
        for thread in self.threads.values():
            thread.join(timeout=2.0)
        if self._vlm_executor is not None:
            self._vlm_executor.shutdown(wait=False)
            self._vlm_executor = None
        logger.info("SafetyPipeline 중지")

    def add_camera(self, camera_id: str, stream_url: str, **kwargs):
        """카메라 추가"""
        return self.camera_manager.add_camera(camera_id, stream_url, **kwargs)

    def add_alert_callback(self, callback: Callable[[SafetyEvent], None]):
        """알람 콜백 등록"""
        self.alert_callbacks.append(callback)

    def add_analysis_callback(self, callback: Callable[[SafetyEvent], None]):
        """알람이 아닌 VLM 관찰 이벤트 콜백 등록.

        COCO pretrained 모델처럼 safety 이벤트를 직접 만들 수 없는 경우에도
        person 탐지 프레임을 대시보드에 전달하고 VLM 분석을 수행하기 위해 사용한다.
        AlertManager에는 전달하지 않아 이메일/캡처 저장 알람 폭주를 방지한다.
        """
        self.analysis_callbacks.append(callback)

    def start(self):
        """파이프라인 시작"""
        self.is_running = True
        self.camera_manager.start_all()

        # 각 카메라별 처리 스레드
        for camera_id in self.camera_manager.cameras.keys():
            self.frame_counters[camera_id] = 0
            thread = threading.Thread(
                target=self._process_camera,
                args=(camera_id,),
                daemon=True
            )
            thread.start()
            self.threads[camera_id] = thread
            logger.info(f"[{camera_id}] 처리 스레드 시작")

    def _process_camera(self, camera_id: str):
        """카메라별 처리 루프"""
        capture = self.camera_manager.get_camera(camera_id)
        if not capture:
            logger.error(f"[{camera_id}] 카메라를 찾을 수 없습니다")
            return

        while self.is_running:
            frame_obj = capture.get_frame(timeout=1.0)
            if frame_obj is None:
                continue

            self.frame_counters[camera_id] += 1

            # Phase 8 M5.3: 동적 프레임 스킵 — 정적 장면이면 더 많이 스킵
            effective_skip = self.skip_frames
            if self.dynamic_skip:
                motion = self._detect_motion(camera_id, frame_obj.image)
                if not motion and self._motion_detected.get(camera_id):
                    effective_skip = self.skip_frames * self.static_skip_multiplier
                self._motion_detected[camera_id] = motion

            # 프레임 스킵
            if self.frame_counters[camera_id] % effective_skip != 0:
                continue

            image = frame_obj.image
            timestamp = frame_obj.timestamp

            if self.use_policy_mode:
                self._process_frame_with_policy(
                    camera_id=camera_id,
                    image=image,
                    timestamp=timestamp,
                )
            else:
                self._process_frame_legacy(
                    camera_id=camera_id,
                    image=image,
                    timestamp=timestamp,
                )

    def _detect_motion(self, camera_id: str, image: np.ndarray) -> bool:
        """프레임 간 모션 감지 — 정적/동적 장면 구분 (Phase 8 M5.3 P1)"""
        try:
            # 그레이스케일 변환 + 리사이즈 (계산 비용 절감)
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (160, 120))

            prev_gray = self._last_frame_gray.get(camera_id)
            if prev_gray is None:
                self._last_frame_gray[camera_id] = gray
                return True  # 첫 프레임은 모션 있다고 가정

            # 프레임 차분
            diff = cv2.absdiff(prev_gray, gray)
            motion_score = float(np.mean(diff)) / 255.0
            self._last_frame_gray[camera_id] = gray
            return motion_score >= self.motion_threshold
        except Exception as exc:
            logger.debug(f"[{camera_id}] 모션 감지 실패: {exc}")
            return True  # 실패 시 모션 있다고 가정 (안전)

    def _process_frame_with_policy(self, camera_id: str, image: np.ndarray, timestamp: datetime):
        """Phase 4 정책 기반 프레임 처리 (MultiModelRegistry + EventPolicyEngine)"""

        if self.model_registry is None or self.policy_engine is None:
            return

        # 다중 모델 추론
        try:
            all_detections = self.model_registry.detect_all(image)
        except Exception as e:
            logger.error(f"[{camera_id}] 다중 모델 추론 실패: {e}")
            return

        with self.latest_result_lock:
            # 대시보드 실시간 스트림은 camera_id 기준으로 최신 결과를 조회한다.
            # policy-mode에서는 여러 모델 결과를 flatten하여 해당 카메라의 최신 결과로 저장한다.
            self.latest_detections[camera_id] = [
                detection
                for detections in all_detections.values()
                for detection in detections
            ]

        # 카메라 프로필 확인
        profile = self._camera_profiles.get(camera_id, {})
        profile_name = profile.get("domain", "construction") if isinstance(profile, dict) else "construction"
        enabled_events = profile.get("events") if isinstance(profile, dict) else None
        zones = profile.get("zones") if isinstance(profile, dict) else None

        # 정책 평가
        try:
            decisions = self.policy_engine.evaluate(
                profile_name=profile_name,
                model_detections=all_detections,
                frame=image,
                camera_id=camera_id,
                timestamp=timestamp,
                zones=zones,
                enabled_events=enabled_events,
            )
        except Exception as e:
            logger.error(f"[{camera_id}] 정책 평가 실패: {e}")
            return

        # 결정된 이벤트 처리
        for decision in decisions:
            event = decision.event
            cooldown_key = f"{camera_id}_{event.event_type}"
            now = time.time()
            cooldown = float(event.cooldown_sec)

            with self._state_lock:
                last = self.last_alerts.get(cooldown_key)
                if last is not None and now - last < cooldown:
                    continue
                self.last_alerts[cooldown_key] = now

            # VLM 필요 여부 확인 (TTL cache 적용)
            if event.vlm_needed:
                self._submit_vlm_analysis(event)

            # 콜백 호출 (alert_level 기반 필터링은 AlertManager에서 처리)
            self._record_event(event)

            logger.info(
                f"[{event.camera_id}] 정책 이벤트 발생: {event.event_type} "
                f"(alert_level={event.alert_level}, "
                f"confidence={event.confidence:.2f})"
            )

            for callback in self.alert_callbacks:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"알람 콜백 오류: {e}")

    def _process_frame_legacy(self, camera_id: str, image: np.ndarray, timestamp: datetime):
        """기존 단일 detector 모드 (하위 호환)"""
        if self.detector is None:
            return

        # 탐지 수행
        detections = self.detector.detect(image)
        with self.latest_result_lock:
            self.latest_detections[camera_id] = detections

        if not detections:
            return

        self._emit_vlm_observation_if_needed(
            camera_id=camera_id,
            timestamp=timestamp,
            detections=detections,
            image=image,
        )

        # 안전장비 위반은 helmet/vest까지 포함한 전체 탐지 결과를 기준으로 판단
        safety_check = self.detector.detect_safety_gear(detections)
        if safety_check["violations"]:
            self._emit_event(
                camera_id=camera_id,
                event_type="safety_gear_violation",
                timestamp=timestamp,
                detections=safety_check["violations"],
                image=image
            )

        # 관심 클래스 필터링
        filtered = [
            d for d in detections
            if d.class_name in self.target_classes and d.class_name != "person"
        ]

        # 이벤트 생성
        for unique_class in set(d.class_name for d in filtered):
            class_dets = [d for d in filtered if d.class_name == unique_class]

            # 이벤트 타입 결정
            event_type = self._determine_event_type(unique_class, class_dets)
            if not event_type:
                continue

            self._emit_event(
                camera_id=camera_id,
                event_type=event_type,
                timestamp=timestamp,
                detections=class_dets,
                image=image
            )

    def _emit_vlm_observation_if_needed(
        self,
        camera_id: str,
        timestamp: datetime,
        detections: list[Detection],
        image: np.ndarray
    ):
        """VLM 후보 프레임을 알람 없이 이벤트 히스토리/대시보드로 전달."""
        if self._vlm_executor is None or not self.vlm_trigger_classes:
            return

        candidate_detections = [
            d for d in detections
            if d.class_name in self.vlm_trigger_classes
        ]
        if not candidate_detections:
            return

        cooldown_key = f"{camera_id}_vlm_observation"
        now = time.time()
        with self._state_lock:
            last = self.last_vlm_observations.get(cooldown_key)
            if last is not None and now - last < self.vlm_cooldown:
                return
            self.last_vlm_observations[cooldown_key] = now

        event = SafetyEvent(
            event_id=f"{camera_id}_vlm_observation_{int(now)}",
            camera_id=camera_id,
            event_type="vlm_analysis_candidate",
            timestamp=timestamp,
            confidence=max((d.confidence for d in candidate_detections), default=0.0),
            detections=candidate_detections,
            image=image.copy(),
            description="VLM 분석 대기"
        )
        self._handle_event(event, notify_alerts=False, notify_analysis=True)

    def _determine_event_type(self, class_name: str, detections: list[Detection]) -> str | None:
        """클래스 이름으로 이벤트 타입 결정"""
        event_type_map = {
            "person": "person_detected",
            "fire": "fire_detected",
            "smoke": "smoke_detected",
            "fall": "fall_detected"
        }

        return event_type_map.get(class_name)

    def _emit_event(
        self,
        camera_id: str,
        event_type: str,
        timestamp: datetime,
        detections: list[Detection],
        image: np.ndarray
    ):
        """쿨다운 확인 후 이벤트 생성"""
        cooldown_key = f"{camera_id}_{event_type}"
        now = time.time()
        with self._state_lock:
            last = self.last_alerts.get(cooldown_key)
            if last is not None and now - last < self.alert_cooldown:
                return
            self.last_alerts[cooldown_key] = now

        event = SafetyEvent(
            event_id=f"{camera_id}_{event_type}_{int(now)}",
            camera_id=camera_id,
            event_type=event_type,
            timestamp=timestamp,
            confidence=max((d.confidence for d in detections), default=0.0),
            detections=detections,
            image=image.copy()
        )

        self._handle_event(event)

    def _handle_event(
        self,
        event: SafetyEvent,
        notify_alerts: bool = True,
        notify_analysis: bool = False
    ):
        """이벤트 처리 및 알람 발송"""
        self._record_event(event)

        log_label = "이벤트" if notify_alerts else "VLM 관찰"
        logger.info(
            f"[{event.camera_id}] {log_label} 발생: {event.event_type} "
            f"(신뢰도: {event.confidence:.2f})"
        )

        # VLM 분석 제출 (비동기, TTL cache 적용)
        self._submit_vlm_analysis(event)

        # 콜백 호출
        if notify_analysis:
            for callback in self.analysis_callbacks:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"분석 콜백 오류: {e}")

        if notify_alerts:
            for callback in self.alert_callbacks:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"알람 콜백 오류: {e}")

    def get_status(self) -> dict:
        """파이프라인 상태"""
        with self._state_lock:
            total = len(self.event_history)
            recent = [e.to_dict() for e in self.event_history[-10:]]
        return {
            "running": self.is_running,
            "cameras": self.camera_manager.get_status(),
            "total_events": total,
            "recent_events": recent,
        }

    def get_latest_detections(self, camera_id: str) -> list[Detection]:
        """카메라별 최신 탐지 결과 조회"""
        with self.latest_result_lock:
            return list(self.latest_detections.get(camera_id, []))
