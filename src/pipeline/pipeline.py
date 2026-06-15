"""
안전 관리 파이프라인

캡처 → 탐지 → 알람 연결
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
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
    # VLM 확장 필드 — Batch1 인터페이스 계약에 따라 추가
    vlm_detected_events: list = field(default_factory=list)
    vlm_recommended_action: str = ""
    vlm_parse_success: bool | None = None  # None=VLM 미실행, True=성공, False=파싱실패
    vlm_raw_response: str = ""
    # Policy engine fields (Phase 4)
    alert_level: str = "low"
    cooldown_sec: float = 30.0
    vlm_needed: bool = False
    source_models: list[str] = field(default_factory=list)
    rule_name: str = ""

    @property
    def vlm_unconfirmed(self) -> bool:
        """정책상 VLM 2차 확인이 필요했으나(vlm_needed) 결과가 없는 상태.

        provider 미가동/실패/큐 drop으로 VLM이 한 번도 산출하지 못하면 parse_success가 None으로
        남는다. 이때 이벤트는 'AI 미확인'이며 — 특히 공개 pretrained 모델의 fire/no_helmet
        false positive가 AI 확인 없이 critical 알람으로 나갈 수 있으므로 — 운영자에게 미확인임을
        명시해 수동 확인을 유도해야 한다.
        """
        return bool(self.vlm_needed) and self.vlm_parse_success is None

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
            # VLM 확장 필드
            "vlm_detected_events": list(self.vlm_detected_events),
            "vlm_recommended_action": self.vlm_recommended_action,
            "vlm_parse_success": self.vlm_parse_success,
            "vlm_unconfirmed": self.vlm_unconfirmed,
            # raw_response는 길 수 있으므로 최대 500자로 잘라 저장
            "vlm_raw_response": (self.vlm_raw_response or "")[:500],
            # Policy engine fields (Phase 4)
            "alert_level": self.alert_level,
            "cooldown_sec": self.cooldown_sec,
            "vlm_needed": self.vlm_needed,
            "source_models": list(self.source_models),
            "rule_name": self.rule_name,
        }


# VLM 큐 backpressure 기본 임계치 (in-flight 작업이 이 수를 초과하면 low/medium 제출 거부)
_VLM_QUEUE_MAX_IN_FLIGHT = 8


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
        # VLM 큐 backpressure 상한 (0이면 무제한)
        vlm_queue_max_in_flight: int = _VLM_QUEUE_MAX_IN_FLIGHT,
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

        # VLM 큐 backpressure — in-flight 게이지 및 drop 카운터
        self._vlm_queue_max_in_flight = vlm_queue_max_in_flight
        self._vlm_in_flight: int = 0          # 현재 제출 후 완료 전 작업 수
        self._vlm_dropped_count: int = 0       # backpressure로 drop된 작업 수
        # VLM 분석/지연 알림 대기 중인 이벤트 id 집합. _record_event의 이미지 eviction이
        # 이 이벤트들의 .image를 비우지 않도록 보호한다(비우면 VLM·캡처 저장이 누락됨).
        self._inflight_event_ids: set[str] = set()
        # VLM 지연 누적 (get_metrics() / get_status() 집계용)
        self._vlm_latency_samples: deque[float] = deque(maxlen=100)

        # YOLO 추론 지연 누적 (카메라별, get_metrics() 집계용)
        # 키: camera_id, 값: deque[float]
        self._yolo_latency_samples: dict[str, deque[float]] = {}

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

    def validate_target_classes(self) -> list[str]:
        """target_classes 중 활성 모델에서 산출 불가능한 클래스를 경고하고 반환한다.

        COCO pretrained 모델은 fire/smoke/fall을 산출할 수 없으므로, 운영자가
        target_classes에 해당 클래스를 두면 이벤트가 영원히 발생하지 않는다.
        이 메서드는 초기화 후 활성 모델이 준비된 시점에 1회 호출해 진단 경고를 낸다.
        반환값: 산출 불가능한 클래스 이름 목록 (경고용).
        """
        reachable: set[str] = set()

        # 단일 detector 모드
        if self.detector is not None and hasattr(self.detector, "classes"):
            reachable.update(self.detector.classes.values())

        # registry 모드 — 로드된 detector는 registry.get_model(name)으로 조회한다
        # (이전 self.model_registry._detectors는 존재하지 않는 속성이라 AttributeError로
        #  start()의 try/except에 먹혀 COCO 한계 경고가 영영 발생하지 않았다).
        if self.model_registry is not None and hasattr(self.model_registry, "load_status"):
            for name, status in self.model_registry.load_status.items():
                if status.loaded:
                    det = self.model_registry.get_model(name)
                    if det is not None and hasattr(det, "classes"):
                        reachable.update(det.classes.values())

        unreachable = [c for c in self.target_classes if c not in reachable]
        if unreachable and reachable:
            # reachable이 비어있으면 아직 모델이 로드되지 않은 것이므로 경고하지 않는다
            logger.warning(
                "target_classes에 활성 모델에서 산출 불가능한 클래스가 있습니다: %s. "
                "이 클래스의 이벤트는 영원히 발생하지 않습니다. "
                "COCO pretrained 모델은 fire/smoke/fall을 지원하지 않으며, "
                "커스텀 safety_custom ONNX 모델이 필요합니다.",
                unreachable,
            )
        return unreachable

    def _analyze_with_vlm(
        self,
        event: SafetyEvent,
        alert_callbacks_after_vlm: list[Callable[[SafetyEvent], None]] | None = None,
    ):
        """별도 스레드에서 VLM 분석 수행.

        alert_callbacks_after_vlm 이 지정되면 VLM 분석 완료 후 해당 콜백들을 호출한다.
        이를 통해 alert(이메일/로그)가 VLM risk_level·설명이 반영된 뒤 발송된다.
        """
        from src.vlm.context_builder import ContextBuilder

        try:
            if self.vlm_provider is None or event.image is None:
                # VLM 불가(provider 없음 또는 이미지가 eviction으로 해제됨) — VLM은 건너뛰되
                # 지연 alert 콜백은 반드시 발송해 알림 누락(특히 위험 이벤트)을 막는다.
                logger.debug(
                    "[%s] VLM 분석 건너뜀 (provider/image 없음) — 지연 알림 발송", event.camera_id
                )
                self._fire_alert_callbacks(event, alert_callbacks_after_vlm)
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

                # 기본 필드 복사
                event.description = result.description
                event.vlm_risk_level = result.risk_level
                event.vlm_confidence = result.confidence
                event.vlm_inference_ms = result.inference_time_ms

                # Batch1 계약: 확장 필드 복사
                event.vlm_detected_events = list(result.detected_events or [])
                event.vlm_recommended_action = result.recommended_action or ""
                event.vlm_raw_response = result.raw_response or ""
                event.vlm_parse_success = bool(result.parse_success)

                # 지연 누적 (get_metrics() 집계용)
                if result.inference_time_ms > 0:
                    with self._state_lock:
                        self._vlm_latency_samples.append(result.inference_time_ms)

                # VLM 결과의 risk_level이 event의 alert_level보다 높으면 업데이트
                risk_priority = {"low": 0, "medium": 1, "high": 2, "critical": 3}
                if risk_priority.get(result.risk_level, 0) > risk_priority.get(event.alert_level, 0):
                    event.alert_level = result.risk_level

                # 파싱 실패 시 경고 — SafetyEvent에 parse_success=False로 이미 표식됨
                if not result.parse_success:
                    logger.warning(
                        "[%s] VLM JSON 파싱 실패 (vlm_parse_success=False). "
                        "원본 응답 일부: %s",
                        event.camera_id,
                        (result.raw_response or "")[:200],
                    )

                logger.info(
                    "[%s] VLM 분석: %s (%dms, risk=%s, parse_ok=%s)",
                    event.camera_id,
                    result.description[:80],
                    int(result.inference_time_ms),
                    result.risk_level,
                    result.parse_success,
                )
            except Exception as e:
                logger.error("VLM 분석 실패 [%s]: %s", event.camera_id, e)
                event.description = "VLM 분석 불가"
                event.vlm_parse_success = False

            # VLM 완료 후 alert 콜백 호출 (브리프 P1: VLM 결과 반영 후 발송)
            self._fire_alert_callbacks(event, alert_callbacks_after_vlm)
        finally:
            # in-flight 게이지 감소 + 이미지 보호 해제 (항상 실행)
            with self._state_lock:
                self._vlm_in_flight = max(0, self._vlm_in_flight - 1)
                self._inflight_event_ids.discard(event.event_id)

    def _fire_alert_callbacks(
        self, event: SafetyEvent, callbacks: list[Callable[[SafetyEvent], None]] | None
    ) -> None:
        """지연 alert 콜백을 안전하게 호출(개별 콜백 예외 격리)."""
        if not callbacks:
            return
        for callback in callbacks:
            try:
                callback(event)
            except Exception as cb_err:
                logger.error("VLM 완료 후 alert 콜백 오류: %s", cb_err)

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

    def get_vlm_queue_stats(self) -> dict[str, Any]:
        """VLM 큐 상태 반환 (pending/in_flight/dropped/avg_latency_ms).

        대시보드·헬스체크에서 VLM backpressure 상태를 표시한다.
        """
        with self._state_lock:
            in_flight = self._vlm_in_flight
            dropped = self._vlm_dropped_count
            samples = list(self._vlm_latency_samples)
            # ThreadPoolExecutor 내부 큐 크기 (pending — 제출됐으나 아직 실행 안 된 것)
            pending = 0
            if self._vlm_executor is not None:
                try:
                    pending = self._vlm_executor._work_queue.qsize()  # type: ignore[attr-defined]
                except Exception:
                    pending = -1  # 접근 불가 시 -1

        avg_latency_ms = round(sum(samples) / len(samples), 1) if samples else 0.0
        return {
            "in_flight": in_flight,
            "pending": pending,
            "dropped": dropped,
            "avg_latency_ms": avg_latency_ms,
            "max_in_flight_limit": self._vlm_queue_max_in_flight,
        }

    def get_metrics(self) -> dict[str, Any]:
        """파이프라인 집계 지표 반환 (M4.1: FPS, inference latency, VLM latency).

        반환 형태:
        {
            "vlm": {avg_latency_ms, total_calls, ...},       # VLM provider 지표
            "vlm_queue": {in_flight, pending, dropped, ...}, # VLM 큐 상태
            "models": {model_name: {...}},                    # 모델별 latency/error
            "cameras": {camera_id: {fps, dropped_frames, ...}},  # 카메라별 지표
        }
        """
        result: dict[str, Any] = {}

        # VLM provider 지표
        if self.vlm_provider is not None and hasattr(self.vlm_provider, "get_metrics"):
            result["vlm"] = self.vlm_provider.get_metrics()
        else:
            result["vlm"] = {}

        # VLM 큐 지표
        result["vlm_queue"] = self.get_vlm_queue_stats()

        # 모델 registry 지표
        if self.model_registry is not None and hasattr(self.model_registry, "get_model_metrics"):
            result["models"] = self.model_registry.get_model_metrics()
        else:
            result["models"] = {}

        # 카메라별 지표 (RTSPCapture.get_status() 위임)
        result["cameras"] = self.camera_manager.get_status()

        return result

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
                # VLM 분석/지연 알림 대기 중(in-flight)인 이벤트의 이미지는 비우지 않는다.
                # 비우면 큐에서 대기하던 _analyze_with_vlm가 image=None으로 조기 반환해
                # VLM 설명·캡처 저장·지연 alert 콜백이 모두 누락되기 때문(화재/낙상 등 위험 이벤트
                # 가 backpressure bypass로 큐 잔류가 길어 특히 표적이 됨).
                if stale.event_id not in self._inflight_event_ids:
                    stale.image = None

    def _submit_vlm_analysis(
        self,
        event: SafetyEvent,
        alert_callbacks_after_vlm: list[Callable[[SafetyEvent], None]] | None = None,
    ) -> bool:
        """VLM 분석을 cache 정책 및 backpressure에 따라 제출한다.

        backpressure 검사 → TTL cache 검사/마킹 → in-flight 증가/등록을 **단일 임계영역**에서
        원자적으로 처리한다. 분리하면 (1) check-then-act TOCTOU로 in-flight 상한이 초과되거나
        (2) backpressure로 drop된 이벤트가 cache를 먼저 마킹해 같은 event_type의 다음 정상
        이벤트를 TTL 동안 억제(오염)하는 문제가 생긴다.
        alert_callbacks_after_vlm: VLM 완료 후 호출할 alert 콜백 목록. 반환: 제출 성공 여부.
        """
        if self._vlm_executor is None or event.image is None:
            return False

        alert_level = (event.alert_level or "low").strip().lower()
        bypass = alert_level in self.vlm_cache_bypass_levels  # critical/high은 항상 제출
        now = time.time()

        with self._state_lock:
            # 1) backpressure: 상한 초과 시 비-bypass 이벤트 drop (cache 마킹 전 — 오염 방지)
            if (
                self._vlm_queue_max_in_flight > 0
                and not bypass
                and self._vlm_in_flight >= self._vlm_queue_max_in_flight
            ):
                self._vlm_dropped_count += 1
                logger.warning(
                    "[%s] VLM 큐 포화 (in_flight=%d, limit=%d) — 이벤트 drop (event_type=%s)",
                    event.camera_id,
                    self._vlm_in_flight,
                    self._vlm_queue_max_in_flight,
                    event.event_type,
                )
                return False

            # 2) TTL cache: bypass는 항상 제출, 그 외는 (camera_id, event_type) 중복 억제
            if bypass:
                self._vlm_cache_stats["bypasses"] += 1
            else:
                cache_key = (event.camera_id, event.event_type)
                last_ts = self._vlm_cache.get(cache_key)
                if last_ts is not None and now - last_ts < self.vlm_cache_ttl_sec:
                    self._vlm_cache_stats["hits"] += 1
                    logger.debug(
                        "[%s] VLM cache hit: event_type=%s", event.camera_id, event.event_type
                    )
                    return False
                self._vlm_cache[cache_key] = now
                self._vlm_cache_stats["misses"] += 1

            # 3) 제출 확정: in-flight 증가 + 이미지 eviction 보호용 id 등록 (원자적)
            self._vlm_in_flight += 1
            self._inflight_event_ids.add(event.event_id)

        try:
            self._vlm_executor.submit(
                self._analyze_with_vlm, event, alert_callbacks_after_vlm
            )
            return True
        except Exception as e:
            with self._state_lock:
                self._vlm_in_flight = max(0, self._vlm_in_flight - 1)
                self._inflight_event_ids.discard(event.event_id)
            logger.error("VLM 제출 실패 [%s]: %s", event.camera_id, e)
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
        # 활성 모델이 준비된 시점에 target_classes 도달 가능성 진단(1회).
        # COCO 모델에서 fire/smoke/fall 같은 산출 불가 클래스를 운영자가 지정한 경우 경고.
        try:
            self.validate_target_classes()
        except Exception as e:
            logger.debug(f"target_classes 검증 건너뜀: {e}")
        self.camera_manager.start_all()

        # 각 카메라별 처리 스레드
        for camera_id in self.camera_manager.cameras:
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

            # Phase 8 M5.3: 동적 프레임 스킵 — 정적 장면이면 더 많이 스킵.
            # 버그 수정: 이전 구현은 '현재 정적 AND 직전이 모션'일 때만 multiplier를 적용해
            # 정적 장면이 연속되면 두 번째 정적 프레임부터 기본값으로 복귀했다.
            # 수정: '현재 정적'이기만 하면 연속해서 multiplier를 유지한다.
            effective_skip = self.skip_frames
            if self.dynamic_skip:
                motion = self._detect_motion(camera_id, frame_obj.image)
                self._motion_detected[camera_id] = motion
                if not motion:
                    effective_skip = self.skip_frames * self.static_skip_multiplier

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
        # enabled_events SSOT: 'enabled_events' 우선, 없으면 'events' fallback.
        # 주의: enabled_events=[](빈 리스트 = "모든 이벤트 비활성")의 의미를 보존해야 한다.
        # `a or b`는 []를 falsy로 처리해 fallback으로 넘어가 필터를 해제하는 논리 반전 버그를 낸다.
        # (EventPolicyEngine은 None=필터없음, []=전부 비활성으로 해석하므로 None과 []를 구분해 전달.)
        if isinstance(profile, dict):
            _ev = profile.get("enabled_events")
            enabled_events = _ev if _ev is not None else profile.get("events")
        else:
            enabled_events = None
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

            self._record_event(event)

            logger.info(
                f"[{event.camera_id}] 정책 이벤트 발생: {event.event_type} "
                f"(alert_level={event.alert_level}, "
                f"confidence={event.confidence:.2f})"
            )

            # VLM 필요 여부 확인 (TTL cache 적용)
            if event.vlm_needed:
                # VLM 완료 후 alert 콜백 호출 — VLM 결과가 반영된 뒤 알림 발송
                submitted = self._submit_vlm_analysis(
                    event,
                    alert_callbacks_after_vlm=list(self.alert_callbacks),
                )
                if not submitted:
                    # VLM 제출 실패(캐시 hit 또는 drop)인 경우 즉시 콜백 호출
                    for callback in self.alert_callbacks:
                        try:
                            callback(event)
                        except Exception as e:
                            logger.error(f"알람 콜백 오류: {e}")
            else:
                # VLM 불필요 → 즉시 콜백 호출
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
        for unique_class in {d.class_name for d in filtered}:
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
        # 관찰 후보는 (1) 대시보드 실시간 표시용 analysis 콜백 emit + (2) 실제 VLM 분석 제출
        # 둘 다 수행해야 한다. _handle_event(notify_analysis=True)는 emit만 하므로 VLM 제출을
        # 별도로 호출한다(이 제출 누락 시 'vlm_analysis_candidate'가 영구히 분석되지 않는 회귀).
        self._handle_event(event, notify_alerts=False, notify_analysis=True)
        self._submit_vlm_analysis(event)

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
        """이벤트 처리 및 알람 발송.

        vlm_needed가 True인 이벤트의 alert 콜백은 VLM 완료 후 호출된다(브리프 P1).
        notify_alerts=True이고 VLM이 없거나 vlm_needed=False인 경우 즉시 호출한다.
        """
        self._record_event(event)

        log_label = "이벤트" if notify_alerts else "VLM 관찰"
        logger.info(
            f"[{event.camera_id}] {log_label} 발생: {event.event_type} "
            f"(신뢰도: {event.confidence:.2f})"
        )

        # analysis 콜백은 항상 즉시 호출 (대시보드 실시간 표시용)
        if notify_analysis:
            for callback in self.analysis_callbacks:
                try:
                    callback(event)
                except Exception as e:
                    logger.error(f"분석 콜백 오류: {e}")

        if notify_alerts:
            if event.vlm_needed and self._vlm_executor is not None:
                # VLM 완료 후 alert 콜백 호출
                submitted = self._submit_vlm_analysis(
                    event,
                    alert_callbacks_after_vlm=list(self.alert_callbacks),
                )
                if not submitted:
                    # 캐시 hit 또는 drop → VLM 없이 즉시 콜백
                    for callback in self.alert_callbacks:
                        try:
                            callback(event)
                        except Exception as e:
                            logger.error(f"알람 콜백 오류: {e}")
            else:
                # VLM 불필요(legacy 즉시 알림 등) → alert 콜백 즉시 호출.
                for callback in self.alert_callbacks:
                    try:
                        callback(event)
                    except Exception as e:
                        logger.error(f"알람 콜백 오류: {e}")
                # baseline 동작 복원: 즉시 알림 이벤트(legacy _emit_event의 fire/smoke/fall 등)도
                # VLM 분석에 제출해 이벤트 히스토리·대시보드가 VLM 설명을 받도록 한다.
                # (알림 콜백은 이미 발송됐으므로 VLM 완료를 기다리지 않는다 — fire-and-forget.)
                self._submit_vlm_analysis(event)
        elif not notify_analysis and not event.vlm_needed:
            # analysis도 alert도 아닌 경우: VLM만 제출
            self._submit_vlm_analysis(event)

    def get_status(self) -> dict:
        """파이프라인 상태 — 운영 지표(FPS·드롭·카메라·VLM 큐·모델 metrics) 포함.

        반환 구조:
        {
            "running": bool,
            "cameras": {camera_id: RTSPCapture.get_status() 딕셔너리},
            "total_events": int,
            "recent_events": list[dict],
            "vlm_queue": get_vlm_queue_stats() 딕셔너리,
            "vlm_cache": get_vlm_cache_stats() 딕셔너리,
            "vlm_provider": OllamaProvider.get_metrics() 딕셔너리 (provider 없으면 {}),
            "models": MultiModelRegistry.get_model_metrics() 딕셔너리 (registry 없으면 {}),
        }
        """
        with self._state_lock:
            total = len(self.event_history)
            recent = [e.to_dict() for e in self.event_history[-10:]]

        return {
            "running": self.is_running,
            "cameras": self.camera_manager.get_status(),
            "total_events": total,
            "recent_events": recent,
            # VLM 큐/지연/drop 지표
            "vlm_queue": self.get_vlm_queue_stats(),
            # VLM TTL 캐시 통계
            "vlm_cache": self.get_vlm_cache_stats(),
            # VLM provider 자체 지표 (총 호출, 성공/실패율, 평균 지연 등)
            "vlm_provider": (
                self.vlm_provider.get_metrics()
                if self.vlm_provider is not None and hasattr(self.vlm_provider, "get_metrics")
                else {}
            ),
            # 모델별 latency/error 지표
            "models": (
                self.model_registry.get_model_metrics()
                if self.model_registry is not None and hasattr(self.model_registry, "get_model_metrics")
                else {}
            ),
        }

    def get_latest_detections(self, camera_id: str) -> list[Detection]:
        """카메라별 최신 탐지 결과 조회"""
        with self.latest_result_lock:
            return list(self.latest_detections.get(camera_id, []))
