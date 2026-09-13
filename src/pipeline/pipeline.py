"""
안전 관리 파이프라인

캡처 → 탐지 → 알람 연결
"""
from __future__ import annotations

import contextlib
import logging
import re
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
from src.events.camera_health import CameraHealthMonitor
from src.events.observations import ObservationStore
from src.events.tamper import TamperMonitor
from src.release_capabilities import release_qualification
from src.tracking import (
    ByteTrackManager,
    LineCrossingCandidate,
    LineCrossingMonitor,
    LoiteringMonitor,
    TrackSnapshot,
)
from src.tracking.behavior_buffer import BehaviorBuffer

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
    # Provisional alert flag (Phase 1)
    provisional_alert: bool = False
    # 알림 생명주기 (Phase 1)
    alert_state: str = "provisional"  # provisional | confirmed | likely_false_positive | needs_review | resolved_false_positive | suppressed
    provisional_published: bool = False
    provisional_sent: bool = False
    provisional_sent_at: datetime | None = None
    verification_summary: dict = field(default_factory=dict)
    verification_reason_codes: list[str] = field(default_factory=list)
    # Verification lifecycle. JPEG inputs are deliberately never retained on events.
    verification_required: bool = False
    verification_mode: str = "none"
    verification_state: str = "not_required"
    verification_quorum: int = 0
    verification_attempts: int = 0
    frame_count: int = 0
    confirm_count: int = 0
    reject_count: int = 0
    error_count: int = 0
    verification_model: str = ""
    verification_model_digest: str = ""
    # Structured runtime metadata for tracking/zone events. Free-text description
    # remains for human-readable notifications; metadata is the machine contract.
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_image: str | None = None

    @property
    def vlm_unconfirmed(self) -> bool:
        """정책상 VLM 2차 확인이 필요했으나(vlm_needed) 결과가 없는 상태.

        provider 미가동/실패/큐 drop으로 VLM이 한 번도 산출하지 못하면 parse_success가 None으로
        남는다. 이때 이벤트는 'AI 미확인'이며 — 특히 공개 pretrained 모델의 fire/no_helmet
        false positive가 AI 확인 없이 critical 알람으로 나갈 수 있으므로 — 운영자에게 미확인임을
        명시해 수동 확인을 유도해야 한다.
        """
        return bool(self.vlm_needed) and self.vlm_parse_success is not True and self.verification_state in {"not_required", "pending", "unverified"}

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
            # 알림 생명주기 (Phase 1)
            "alert_state": self.alert_state,
            "provisional_published": self.provisional_published,
            "provisional_sent": self.provisional_sent,
            "provisional_sent_at": self.provisional_sent_at.isoformat() if self.provisional_sent_at else None,
            "verification_summary": self.verification_summary,
            "verification_reason_codes": self.verification_reason_codes,
            "verification_required": self.verification_required,
            "verification_mode": self.verification_mode,
            "verification_state": self.verification_state,
            "verification_quorum": self.verification_quorum,
            "verification_attempts": self.verification_attempts,
            "frame_count": self.frame_count,
            "confirm_count": self.confirm_count,
            "reject_count": self.reject_count,
            "error_count": self.error_count,
            "verification_model": self.verification_model,
            "verification_model_digest": self.verification_model_digest,
            "metadata": dict(self.metadata) if self.metadata else {},
            "evidence_image": self.evidence_image,
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
        vlm_queue_hard_limit: int | None = None,
        vlm_shutdown_grace_sec: float = 10.0,
        vlm_frame_max_frames: int = 3,
        vlm_frame_max_dimension: int = 640,
        vlm_frame_jpeg_quality: int = 80,
        vlm_frame_total_bytes: int = 3 * 1024 * 1024,
        vlm_frame_window_sec: float = 5.0,
        camera_stale_after_sec: float = 10.0,
        camera_disconnect_after_sec: float = 30.0,
        observation_store: ObservationStore | None = None,
        tamper_duration_sec: float = 5.0,
        tamper_recovery_sec: float = 3.0,
        track_manager: ByteTrackManager | None = None,
        behavior_settings: dict[str, Any] | None = None,
        vlm_configured_enabled: bool = False,
        vlm_runtime_qualified: bool = False,
        vlm_provider_available: bool | None = None,
        vlm_reason_category: str = "disabled",
    ):
        self.detector = detector
        self.skip_frames = skip_frames
        self.alert_cooldown = alert_cooldown
        self.target_classes = set(target_classes or ["person", "fire", "smoke", "fall"])
        # Generic person observations must never become VLM jobs by default.
        # Legacy deployments can opt in explicitly with ["person"].
        self.vlm_trigger_classes = set(vlm_trigger_classes or [])
        self.vlm_cooldown = float(vlm_cooldown if vlm_cooldown is not None else alert_cooldown)

        # Phase 4: policy-based pipeline mode
        self.model_registry = model_registry
        self.policy_engine = policy_engine
        self.use_policy_mode = (model_registry is not None and policy_engine is not None)
        # Legacy 단일-detector 경로는 하위 호환용으로만 유지한다. 향후 명시적
        # opt-in(KSI_CCTV_LEGACY_FALLBACK=1)으로 전환 예정 — 신규 코드는 policy-mode를 사용하라.
        self._legacy_warned = False

        # Camera profile for policy evaluation (set via set_camera_profile)
        self._camera_profiles: dict[str, dict] = {}
        self._camera_generations: dict[str, int] = {}
        self.camera_health_monitor = CameraHealthMonitor(
            stale_after_sec=camera_stale_after_sec,
            disconnect_after_sec=camera_disconnect_after_sec,
        )
        self.observation_store = observation_store or ObservationStore()
        self.tamper_monitor = TamperMonitor(
            duration_sec=tamper_duration_sec,
            recovery_sec=tamper_recovery_sec,
        )
        self.track_manager = track_manager or ByteTrackManager()
        self.line_crossing_monitor = LineCrossingMonitor()
        self.loitering_monitor = LoiteringMonitor()
        self.latest_tracks: dict[str, list[TrackSnapshot]] = {}
        self._latest_tracking_meta: dict[str, dict[str, Any]] = {}
        self._tracking_errors: dict[str, int] = {}
        self.behavior_settings = dict(behavior_settings or {})
        self._behavior_enabled = bool(self.behavior_settings.get("enabled", False))
        self._behavior_iou_threshold = float(self.behavior_settings.get("iou_threshold", 0.5))
        self._behavior_stride = max(1, int(self.behavior_settings.get("inference_stride", 5)))
        self._behavior_buffers: dict[str, BehaviorBuffer] = {}
        self._behavior_ticks: dict[tuple[str, int, str], int] = {}
        self._behavior_counters = {
            "pose_failures": 0, "action_failures": 0, "unavailable": 0,
            "unmatched": 0, "ambiguous": 0, "inferences": 0,
        }
        self._snapshot_sanitizer: Callable[[np.ndarray], np.ndarray] | None = None
        self._snapshot_sanitizer_required = False

        self.camera_manager = CameraManager()
        self.is_running = False
        self.threads: dict[str, threading.Thread] = {}
        self._camera_runtime_lock = threading.RLock()
        self._tracking_state_lock = threading.RLock()
        self._event_dispatch_lock = threading.RLock()
        self._camera_stop_events: dict[str, threading.Event] = {}

        # 알람 콜백
        self.alert_callbacks: list[Callable[[SafetyEvent], bool | None]] = []
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
        self._state_lock = threading.RLock()
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

        # VLM 분석 (enable_vlm 호출 시 주입 — 선택적 컴포넌트라 Any로 둔다)
        self.vlm_provider: Any = None
        self.context_builder: Any = None
        self._vlm_executor: ThreadPoolExecutor | None = None
        self._vlm_configured_enabled = bool(vlm_configured_enabled)
        self._vlm_runtime_qualified = bool(vlm_runtime_qualified)
        self._vlm_provider_available = vlm_provider_available
        self._vlm_reason_category = str(vlm_reason_category)

        # VLM 큐 backpressure — in-flight 게이지 및 drop 카운터
        self._vlm_queue_max_in_flight = vlm_queue_max_in_flight
        self._vlm_queue_hard_limit = max(1, int(vlm_queue_hard_limit or max(vlm_queue_max_in_flight, 1)))
        self._vlm_shutdown_grace_sec = max(0.0, float(vlm_shutdown_grace_sec))
        self._vlm_in_flight: int = 0          # 현재 제출 후 완료 전 작업 수
        self._vlm_dropped_count: int = 0       # backpressure로 drop된 작업 수
        # VLM 분석/지연 알림 대기 중인 이벤트 id 집합. _record_event의 이미지 eviction이
        # 이 이벤트들의 .image를 비우지 않도록 보호한다(비우면 VLM·캡처 저장이 누락됨).
        self._inflight_event_ids: set[str] = set()
        self._inflight_events: dict[
            str,
            tuple[SafetyEvent, list[Callable[[SafetyEvent], bool | None]] | None],
        ] = {}
        self._forced_shutdown_event_ids: set[str] = set()
        # VLM 지연 누적 (get_metrics() / get_status() 집계용)
        self._vlm_latency_samples: deque[float] = deque(maxlen=100)
        self._vlm_frame_max_frames = max(1, int(vlm_frame_max_frames))
        self._vlm_frame_max_dimension = max(32, int(vlm_frame_max_dimension))
        self._vlm_frame_jpeg_quality = min(100, max(1, int(vlm_frame_jpeg_quality)))
        self._vlm_frame_total_bytes = max(1, int(vlm_frame_total_bytes))
        self._vlm_frame_window_sec = max(0.0, float(vlm_frame_window_sec))
        self._vlm_frame_buffers: dict[str, deque[tuple[float, bytes]]] = {}
        self._vlm_lifecycle_counts = dict.fromkeys(("pending", "confirmed", "rejected", "unverified", "timeout", "provider_down", "malformed", "queue_drop", "insufficient_frames", "frames_total"), 0)

        # YOLO 추론 지연 누적 (카메라별, get_metrics() 집계용)
        # 키: camera_id, 값: deque[float]
        self._yolo_latency_samples: dict[str, deque[float]] = {}

        logger.info("SafetyPipeline 초기화 완료")

    def enable_vlm(self, provider, prompt_template_path: str, *, max_concurrent: int = 2):
        """VLM 분석 활성화"""
        from src.vlm.context_builder import ContextBuilder
        from src.vlm.prompt_selector import PromptSelector
        from src.vlm.vlm_inference import VLMProvider

        if not isinstance(provider, VLMProvider):
            logger.error("VLM provider does not implement VLMProvider")
            self.set_vlm_runtime_intent(provider_available=False, reason_category="provider_invalid")
            return
        if isinstance(max_concurrent, bool) or not isinstance(max_concurrent, int) or not 1 <= max_concurrent <= 32:
            raise ValueError("VLM max_concurrent must be an integer within [1, 32]")
        capabilities = provider.capabilities()
        digest = str(capabilities.get("model_digest", ""))
        if not capabilities.get("runtime_qualified") or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            logger.error("VLM provider is not runtime qualified")
            self.set_vlm_runtime_intent(runtime_qualified=False, provider_available=False, reason_category="unqualified")
            return

        self.vlm_provider = provider
        # 기본 컨텍스트 빌더 (하위 호환성)
        self.context_builder = ContextBuilder(prompt_template_path)
        # 프롬프트 선택기 초기화
        self.prompt_selector = PromptSelector()
        self._vlm_executor = ThreadPoolExecutor(max_workers=max_concurrent, thread_name_prefix="vlm")
        available = bool(provider.is_available())
        self.set_vlm_runtime_intent(
            configured_enabled=True,
            runtime_qualified=True,
            provider_available=available,
            reason_category="ready" if available else "provider_unavailable",
        )
        logger.info("VLM enabled (provider=%s, available=%s)", getattr(provider, "model", type(provider).__name__), available)

    def set_vlm_runtime_intent(
        self,
        *,
        configured_enabled: bool | None = None,
        runtime_qualified: bool | None = None,
        provider_available: bool | None = None,
        reason_category: str | None = None,
    ) -> None:
        """Record sanitized VLM startup state without retaining endpoint diagnostics."""
        if configured_enabled is not None:
            self._vlm_configured_enabled = bool(configured_enabled)
        if runtime_qualified is not None:
            self._vlm_runtime_qualified = bool(runtime_qualified)
        if provider_available is not None:
            self._vlm_provider_available = bool(provider_available)
        if reason_category is not None:
            self._vlm_reason_category = str(reason_category)

    def _remember_vlm_frame(self, camera_id: str, image: np.ndarray, timestamp: datetime) -> None:
        if self.vlm_provider is None:
            return
        height, width = image.shape[:2]
        scale = min(1.0, self._vlm_frame_max_dimension / max(height, width))
        frame = cv2.resize(image, (round(width * scale), round(height * scale))) if scale < 1 else image
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self._vlm_frame_jpeg_quality])
        if not ok:
            return
        item = (timestamp.timestamp(), encoded.tobytes())
        with self._state_lock:
            buffer = self._vlm_frame_buffers.setdefault(camera_id, deque())
            buffer.append(item)
            cutoff = item[0] - self._vlm_frame_window_sec
            while buffer and (len(buffer) > self._vlm_frame_max_frames or buffer[0][0] < cutoff):
                buffer.popleft()
            while sum(len(data) for _, data in buffer) > self._vlm_frame_total_bytes:
                buffer.popleft()

    def _select_vlm_frames(self, event: SafetyEvent) -> list[bytes]:
        required = 3 if event.verification_mode == "three_frame" else 1
        with self._state_lock:
            frames = list(self._vlm_frame_buffers.get(event.camera_id, ()))
        # JPEG byte identity makes duplicate retained frames ineligible.
        selected = [data for _, data in frames]
        if not selected and not event.verification_required and event.image is not None:
            ok, encoded = cv2.imencode(".jpg", event.image)
            selected = [encoded.tobytes()] if ok else []
        return selected[-required:]

    def _clear_vlm_frames(self, camera_id: str | None = None) -> None:
        with self._state_lock:
            if camera_id is None:
                self._vlm_frame_buffers.clear()
            else:
                self._vlm_frame_buffers.pop(camera_id, None)

    def set_camera_profile(self, camera_id: str, profile: dict):
        """카메라별 정책 프로필 설정 (Phase 4).

        정식형 {domain, enabled_events, zones, detection_type}으로 정규화해
        저장한다. 구 별칭(events→enabled_events, detection_type/profile→domain)도
        받아들인다. enabled_events=[] (전부 비활성)와 None (필터 없음)은 구분해
        보존한다.
        """
        normalized = dict(profile or {})
        normalized["domain"] = (
            normalized.get("domain")
            or normalized.get("detection_type")
            or normalized.get("profile")
            or "construction"
        )
        if "enabled_events" not in normalized:
            normalized["enabled_events"] = normalized.get("events")
        normalized["detection_type"] = normalized.get("detection_type") or normalized["domain"]
        profile = normalized
        with self._camera_runtime_lock:
            if self._camera_profiles.get(camera_id) != profile:
                self._camera_generations[camera_id] = (
                    self._camera_generations.get(camera_id, 0) + 1
                )
                with self._tracking_state_lock:
                    self._reset_policy_state(camera_id)
                    self.track_manager.reset(camera_id)
                    self.line_crossing_monitor.reset(camera_id)
                    self.loitering_monitor.reset(camera_id)
                    self._reset_behavior_state(camera_id)
                self._clear_vlm_frames(camera_id)
                with self.latest_result_lock:
                    self.latest_tracks.pop(camera_id, None)
                    self._latest_tracking_meta.pop(camera_id, None)
                with self._state_lock:
                    self._tracking_errors.pop(camera_id, None)
            self._camera_profiles[camera_id] = profile

    def _reset_policy_state(self, camera_id: str | None = None) -> None:
        if self.policy_engine is None:
            return
        registry = getattr(self.policy_engine, "rule_evaluators", None)
        reset = getattr(registry, "reset", None)
        if callable(reset):
            reset(camera_id)

    def _reset_behavior_state(self, camera_id: str | None = None) -> None:
        for buffer in self._behavior_buffers.values():
            buffer.reset(camera_id)
        if camera_id is None:
            self._behavior_ticks.clear()
        else:
            self._behavior_ticks = {
                key: value for key, value in self._behavior_ticks.items() if key[0] != camera_id
            }

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
        alert_callbacks_after_vlm: list[Callable[[SafetyEvent], bool | None]] | None = None,
        frames: list[bytes] | None = None,
    ):
        """별도 스레드에서 VLM 분석 수행.

        alert_callbacks_after_vlm 이 지정되면 VLM 분석 완료 후 해당 콜백들을 호출한다.
        이를 통해 alert(이메일/로그)가 VLM risk_level·설명이 반영된 뒤 발송된다.
        """
        from src.vlm.context_builder import ContextBuilder

        result = None
        try:
            if frames is None and event.image is not None:
                ok, encoded = cv2.imencode(".jpg", event.image)
                frames = [encoded.tobytes()] if ok else []
            if self.vlm_provider is None or not frames:
                # VLM 불가(provider 없음 또는 이미지가 eviction으로 해제됨) — VLM은 건너뛰되
                # 지연 alert 콜백은 반드시 발송해 알림 누락(특히 위험 이벤트)을 막는다.
                logger.debug(
                    "[%s] VLM 분석 건너뜀 (provider/image 없음) — 지연 알림 발송", event.camera_id
                )
                self._update_event_state_from_vlm(event, None)
                with self._state_lock:
                    forced_shutdown = event.event_id in self._forced_shutdown_event_ids
                if not forced_shutdown:
                    self._fire_alert_callbacks(event, alert_callbacks_after_vlm)
                return

            try:
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

                analyze_frames = getattr(self.vlm_provider, "analyze_frames", None)
                results = analyze_frames(frames, prompt) if callable(analyze_frames) else None
                if not isinstance(results, list):
                    results = [self.vlm_provider.analyze(frames[0], prompt)]
                result = results[0] if results else None

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
                capabilities = self.vlm_provider.capabilities() if hasattr(self.vlm_provider, "capabilities") else {}
                event.verification_model = str(getattr(self.vlm_provider, "model", ""))
                event.verification_model_digest = str(capabilities.get("model_digest", ""))
                self._apply_verification_results(event, results)

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
                        "[%s] VLM JSON parsing failed (vlm_parse_success=False)",
                        event.camera_id,
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
                logger.error("VLM analysis failed [%s]: %s", event.camera_id, type(e).__name__)
                event.description = "VLM 분석 불가"
                event.vlm_parse_success = False
                event.verification_summary = {"error": str(e)}
                event.verification_reason_codes = ["VLM_ERROR"]

            # VLM 검증 결과로 알림 상태 업데이트
            if result is None:
                self._finalize_verification(event, "unverified", "VLM_EMPTY")

            # VLM 완료 후 alert 콜백 호출 (브리프 P1: VLM 결과 반영 후 발송)
            with self._state_lock:
                forced_shutdown = event.event_id in self._forced_shutdown_event_ids
            if not forced_shutdown:
                self._fire_alert_callbacks(event, alert_callbacks_after_vlm)
        finally:
            # in-flight 게이지 감소 + 이미지 보호 해제 (항상 실행)
            with self._state_lock:
                self._vlm_in_flight = max(0, self._vlm_in_flight - 1)
                self._inflight_event_ids.discard(event.event_id)
                self._inflight_events.pop(event.event_id, None)

    def _update_event_state_from_vlm(self, event: SafetyEvent, vlm_result: Any) -> None:
        """VLM 검증 결과로 이벤트 상태 업데이트.

        생명안전 이벤트(provisional_alert)는 이미 잠정 알림이 발송됨.
        VLM 결과에 따라 상태만 업데이트:
        - CONFIRM → confirmed
        - REJECT → likely_false_positive (알림 억제는 이미 발송됐으므로 상태만 변경)
        - ABSTAIN → needs_review
        - ERROR/parse_fail → needs_review (억제하지 않음)
        """
        if vlm_result is None:
            self._finalize_verification(event, "unverified", "NO_VLM")
            return

        # VLM 결과에서 검증 판정 추출
        verdict = getattr(vlm_result, "verdict", "abstain")
        confidence = getattr(vlm_result, "confidence", 0.0)
        risk_level = getattr(vlm_result, "risk_level", "low")
        parse_success = getattr(vlm_result, "parse_success", False)

        event.verification_summary = {
            "verdict": verdict,
            "confidence": confidence,
            "risk_level": risk_level,
            "parse_success": parse_success,
        }
        event.verification_reason_codes = [f"VLM_{verdict.upper()}"]

        # provisional 상태인 경우에만 상태 전이
        if event.alert_state == "provisional":
            if verdict == "confirm":
                event.alert_state = "confirmed"
            elif verdict == "reject":
                event.alert_state = "likely_false_positive"
            elif verdict == "abstain":
                event.alert_state = "needs_review"
            else:  # error or parse_fail
                event.alert_state = "needs_review"
                event.verification_reason_codes.append("VLM_ERROR")

        # VLM 위험도가 높으면 alert_level 승격
        risk_priority = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        vlm_risk = getattr(vlm_result, "risk_level", "low")
        if risk_priority.get(vlm_risk, 0) > risk_priority.get(event.alert_level, 0):
            event.alert_level = vlm_risk

    def _finalize_verification(self, event: SafetyEvent, state: str, reason: str) -> None:
        event.verification_state = state
        event.verification_reason_codes = [reason]
        event.verification_summary = {"status": state, "reason": reason,
                                      "frames": event.frame_count, "confirm": event.confirm_count,
                                      "reject": event.reject_count, "error": event.error_count}
        event.alert_state = {"pending": "provisional", "confirmed": "confirmed",
                             "rejected": "likely_false_positive", "unverified": "unverified"}.get(state, event.alert_state)
        with self._state_lock:
            self._vlm_lifecycle_counts[state] = self._vlm_lifecycle_counts.get(state, 0) + 1
            if reason == "INSUFFICIENT_FRAMES":
                self._vlm_lifecycle_counts["insufficient_frames"] += 1
            if reason == "QUEUE_DROP":
                self._vlm_lifecycle_counts["queue_drop"] += 1
            if reason in {"TIMEOUT", "FORCED_SHUTDOWN"}:
                self._vlm_lifecycle_counts["timeout"] += 1
            if reason == "MALFORMED":
                self._vlm_lifecycle_counts["malformed"] += 1
            if reason == "PROVIDER_UNAVAILABLE":
                self._vlm_lifecycle_counts["provider_down"] += 1

    def _apply_verification_results(self, event: SafetyEvent, results: list[Any]) -> None:
        event.verification_attempts = len(results)
        event.frame_count = len(results)
        event.confirm_count = sum(getattr(item, "verdict", "") == "confirm" and getattr(item, "parse_success", False) for item in results)
        event.reject_count = sum(getattr(item, "verdict", "") == "reject" and getattr(item, "parse_success", False) for item in results)
        event.error_count = sum(not getattr(item, "parse_success", False) or getattr(item, "verdict", "") == "error" for item in results)
        with self._state_lock:
            self._vlm_lifecycle_counts["frames_total"] += event.frame_count
        if event.confirm_count >= event.verification_quorum:
            self._finalize_verification(event, "confirmed", "VLM_CONFIRM")
        elif event.reject_count >= event.verification_quorum and event.metadata.get("verification_allow_reject", False):
            self._finalize_verification(event, "rejected", "VLM_REJECT")
        elif event.error_count:
            self._finalize_verification(event, "unverified", self._failure_reason(results))
        else:
            self._finalize_verification(event, "unverified", "VLM_INCONCLUSIVE")

    # provider가 구분해 보낸 실패 종류 → 이벤트 사유 코드.
    # 미상(구형 provider·미설정)이면 기존 동작대로 MALFORMED로 둔다.
    _FAILURE_REASON_BY_KIND = {
        "provider_unavailable": "PROVIDER_UNAVAILABLE",
        "timeout": "TIMEOUT",
        "malformed": "MALFORMED",
        "input_rejected": "INPUT_REJECTED",
    }

    def _failure_reason(self, results: list[Any]) -> str:
        """실패 결과들의 실패 종류를 사유 코드로 옮긴다.

        종류가 섞이면 운영자가 먼저 손봐야 할 쪽(provider 장애 > 타임아웃 >
        입력 거부 > 응답 형식)을 우선한다. provider가 죽었는데 "응답 형식 오류"로
        보고하면 엉뚱한 곳을 조사하게 된다.
        """
        kinds = {
            getattr(item, "failure_kind", "")
            for item in results
            if not getattr(item, "parse_success", False) or getattr(item, "verdict", "") == "error"
        }
        for kind in ("provider_unavailable", "timeout", "input_rejected", "malformed"):
            if kind in kinds:
                return self._FAILURE_REASON_BY_KIND[kind]
        return "MALFORMED"

    def _fire_alert_callbacks(
        self, event: SafetyEvent, callbacks: list[Callable[[SafetyEvent], bool | None]] | None
    ) -> bool:
        """지연 alert 콜백을 안전하게 호출(개별 콜백 예외 격리)."""
        if not callbacks:
            return False
        durable_accept = False
        for callback in callbacks:
            try:
                durable_accept = callback(event) is True or durable_accept
            except Exception as cb_err:
                logger.error("VLM 완료 후 alert 콜백 오류: %s", cb_err)
        return durable_accept

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
            "hard_limit": self._vlm_queue_hard_limit,
            "frame_buffer_count": sum(len(items) for items in self._vlm_frame_buffers.values()),
            "frame_buffer_bytes": sum(len(data) for items in self._vlm_frame_buffers.values() for _, data in items),
            "qualified": bool(self.vlm_provider and self.vlm_provider.capabilities().get("runtime_qualified")),
            **dict(self._vlm_lifecycle_counts),
        }

    def get_ai_runtime_status(self) -> dict[str, Any]:
        """Return the safe, aggregate AI runtime view for APIs and health checks."""
        queue = self.get_vlm_queue_stats()
        behavior = self._behavior_status()
        behavior_counters = behavior["counters"]
        buffers = behavior["buffers"]
        provider_metrics = (
            self.vlm_provider.get_metrics()
            if self.vlm_provider is not None and hasattr(self.vlm_provider, "get_metrics")
            else {}
        )
        if not isinstance(provider_metrics, dict):
            provider_metrics = {}
        behavior_available = bool(
            self.model_registry is not None
            and getattr(self.model_registry, "pose_detectors", {})
            and getattr(self.model_registry, "action_classifiers", {})
        )
        return {
            "behavior": {
                "enabled": behavior["enabled"],
                "available": behavior_available,
                "runtime_errors": {
                    "pose": behavior_counters["pose_failures"],
                    "action": behavior_counters["action_failures"],
                },
                "unavailable": behavior_counters["unavailable"],
                "buffer": {
                    "stale_after_sec": float(self.behavior_settings.get("stale_after_sec", 10.0)),
                    "max_tracks_per_camera": int(self.behavior_settings.get("max_tracks_per_camera", 100)),
                    "sequence_lengths": {
                        name: buffer.sequence_length for name, buffer in self._behavior_buffers.items()
                    },
                    "occupancy": {
                        "buffers": len(buffers),
                        "tracks": sum(item.get("tracks", 0) for item in buffers.values()),
                        "observations": sum(item.get("observations", 0) for item in buffers.values()),
                    },
                },
            },
            "vlm": {
                "configured_enabled": self._vlm_configured_enabled,
                "enabled": self.vlm_provider is not None and self._vlm_executor is not None,
                "runtime_qualified": self._vlm_runtime_qualified,
                "provider_available": self._vlm_provider_available,
                "reason_category": self._vlm_reason_category,
                "queue": {
                    "in_flight": queue["in_flight"],
                    "pending": queue["pending"],
                    "soft_limit": queue["max_in_flight_limit"],
                    "hard_limit": queue["hard_limit"],
                    "saturated": queue["in_flight"] >= queue["hard_limit"],
                    "dropped": queue["dropped"],
                },
                "lifecycle": {
                    name: max(queue[name], int(provider_metrics.get(f"{name}_count", 0) or 0))
                    for name in ("pending", "confirmed", "rejected", "unverified", "provider_down", "malformed", "queue_drop")
                },
            },
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
        result["behavior"] = self._behavior_status()

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
        alert_callbacks_after_vlm: list[Callable[[SafetyEvent], bool | None]] | None = None,
    ) -> bool:
        """VLM 분석을 cache 정책 및 backpressure에 따라 제출한다.

        backpressure 검사 → TTL cache 검사/마킹 → in-flight 증가/등록을 **단일 임계영역**에서
        원자적으로 처리한다. 분리하면 (1) check-then-act TOCTOU로 in-flight 상한이 초과되거나
        (2) backpressure로 drop된 이벤트가 cache를 먼저 마킹해 같은 event_type의 다음 정상
        이벤트를 TTL 동안 억제(오염)하는 문제가 생긴다.
        alert_callbacks_after_vlm: VLM 완료 후 호출할 alert 콜백 목록. 반환: 제출 성공 여부.
        """
        if self._vlm_executor is None or self.vlm_provider is None:
            self._finalize_verification(event, "unverified", "PROVIDER_UNAVAILABLE")
            return False
        frames = self._select_vlm_frames(event)
        required_frames = 3 if event.verification_mode == "three_frame" else 1
        if len(frames) < required_frames:
            self._finalize_verification(event, "unverified", "INSUFFICIENT_FRAMES")
            return False

        alert_level = (event.alert_level or "low").strip().lower()
        bypass = alert_level in self.vlm_cache_bypass_levels  # critical/high은 항상 제출
        now = time.time()

        failure_reason = ""
        with self._state_lock:
            if self._vlm_in_flight >= self._vlm_queue_hard_limit:
                self._vlm_dropped_count += 1
                failure_reason = "QUEUE_DROP"
            # 1) backpressure: 상한 초과 시 비-bypass 이벤트 drop (cache 마킹 전 — 오염 방지)
            elif (
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
                failure_reason = "QUEUE_DROP"

            # 2) TTL cache: bypass는 항상 제출, 그 외는 (camera_id, event_type) 중복 억제
            if not failure_reason and bypass:
                self._vlm_cache_stats["bypasses"] += 1
            elif not failure_reason:
                cache_key = (event.camera_id, event.event_type)
                last_ts = self._vlm_cache.get(cache_key)
                if last_ts is not None and now - last_ts < self.vlm_cache_ttl_sec:
                    self._vlm_cache_stats["hits"] += 1
                    logger.debug(
                        "[%s] VLM cache hit: event_type=%s", event.camera_id, event.event_type
                    )
                    failure_reason = "CACHE_SKIPPED"
                else:
                    self._vlm_cache[cache_key] = now
                    self._vlm_cache_stats["misses"] += 1

            # 3) 제출 확정: in-flight 증가 + 이미지 eviction 보호용 id 등록 (원자적)
            if not failure_reason:
                self._vlm_in_flight += 1
                self._inflight_event_ids.add(event.event_id)
                self._inflight_events[event.event_id] = (event, alert_callbacks_after_vlm)

        if failure_reason:
            self._finalize_verification(event, "unverified", failure_reason)
            return False

        try:
            self._vlm_executor.submit(
                self._analyze_with_vlm, event, alert_callbacks_after_vlm, frames
            )
            return True
        except Exception as e:
            with self._state_lock:
                self._vlm_in_flight = max(0, self._vlm_in_flight - 1)
                self._inflight_event_ids.discard(event.event_id)
                self._inflight_events.pop(event.event_id, None)
            logger.error("VLM 제출 실패 [%s]: %s", event.camera_id, e)
            self._finalize_verification(event, "unverified", "SUBMIT_ERROR")
            return False

    def stop(self):
        """파이프라인 중지"""
        with self._event_dispatch_lock, self._camera_runtime_lock:
            self.is_running = False
            for camera_id in set(self._camera_profiles) | set(self.threads):
                self._camera_generations[camera_id] = (
                    self._camera_generations.get(camera_id, 0) + 1
                )
            stop_events = list(self._camera_stop_events.values())
            threads = list(self.threads.values())
            self.threads.clear()
        for stop_event in stop_events:
            stop_event.set()
        self.camera_manager.stop_all()
        for thread in threads:
            thread.join(timeout=2.0)
        if self._vlm_executor is not None:
            deadline = time.monotonic() + self._vlm_shutdown_grace_sec
            while time.monotonic() < deadline:
                with self._state_lock:
                    if self._vlm_in_flight == 0:
                        break
                time.sleep(0.05)

            with self._state_lock:
                remaining = list(self._inflight_events.items())
                self._forced_shutdown_event_ids.update(event_id for event_id, _ in remaining)
                self._vlm_in_flight = 0
                self._inflight_event_ids.clear()
                self._inflight_events.clear()

            # 제한 시간 내 검증되지 않은 이벤트도 상태 유실 없이 수동 검토 대상으로 발행한다.
            for _event_id, (event, callbacks) in remaining:
                self._finalize_verification(event, "unverified", "FORCED_SHUTDOWN")
                self._fire_alert_callbacks(event, callbacks)

            self._vlm_executor.shutdown(wait=False, cancel_futures=True)
            self._vlm_executor = None
        self._reset_policy_state()
        self._clear_vlm_frames()
        self.camera_health_monitor.reset()
        self.tamper_monitor.reset()
        with self._tracking_state_lock:
            self.track_manager.reset()
            self.line_crossing_monitor.reset()
            self.loitering_monitor.reset()
            self._reset_behavior_state()
        with self.latest_result_lock:
            self.latest_tracks.clear()
            self._latest_tracking_meta.clear()
        with self._state_lock:
            self._tracking_errors.clear()
        close_observations = getattr(self.observation_store, "close", None)
        if callable(close_observations):
            close_observations()
        logger.info("SafetyPipeline 중지")

    def add_camera(self, camera_id: str, stream_url: str, **kwargs):
        """카메라 추가. 실행 중이면 캡처와 처리 스레드도 즉시 시작한다."""
        with self._camera_runtime_lock:
            capture = self.camera_manager.add_camera(camera_id, stream_url, **kwargs)
            stop_event = threading.Event()
            self._camera_stop_events[camera_id] = stop_event
            self.frame_counters[camera_id] = 0
            if self.is_running:
                capture.start()
                self._start_camera_thread(camera_id, capture, stop_event)
            return capture

    def _start_camera_thread(
        self, camera_id: str, capture: Any, stop_event: threading.Event
    ) -> None:
        thread = threading.Thread(
            target=self._process_camera,
            args=(camera_id, capture, stop_event),
            daemon=True,
            name=f"camera-{camera_id}",
        )
        self.threads[camera_id] = thread
        thread.start()

    def remove_camera(self, camera_id: str) -> bool:
        """실행 중인 특정 카메라만 안전하게 중지하고 상태를 정리한다."""
        with self._camera_runtime_lock:
            self._camera_generations[camera_id] = (
                self._camera_generations.get(camera_id, 0) + 1
            )
            stop_event = self._camera_stop_events.pop(camera_id, None)
            if stop_event is not None:
                stop_event.set()
            capture = self.camera_manager.remove_camera(camera_id)
            thread = self.threads.pop(camera_id, None)
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        with self._state_lock:
            self.last_alerts = {
                key: value for key, value in self.last_alerts.items()
                if not key.startswith(f"{camera_id}_")
            }
            self.last_vlm_observations = {
                key: value for key, value in self.last_vlm_observations.items()
                if not key.startswith(f"{camera_id}_")
            }
        self._reset_policy_state(camera_id)
        self.camera_health_monitor.reset(camera_id)
        self.tamper_monitor.reset(camera_id)
        with self._tracking_state_lock:
            self.track_manager.reset(camera_id)
            self.line_crossing_monitor.reset(camera_id)
            self.loitering_monitor.reset(camera_id)
            self._reset_behavior_state(camera_id)
        with self.latest_result_lock:
            self.latest_tracks.pop(camera_id, None)
            self._latest_tracking_meta.pop(camera_id, None)
        with self._state_lock:
            self._tracking_errors.pop(camera_id, None)
        self._clear_vlm_frames(camera_id)
        self.frame_counters.pop(camera_id, None)
        self._camera_profiles.pop(camera_id, None)
        self._last_frame_gray.pop(camera_id, None)
        self._motion_detected.pop(camera_id, None)
        return capture is not None

    def apply_camera(
        self,
        camera_id: str,
        stream_url: str,
        *,
        profile: dict | None = None,
        target_fps: int = 10,
        resize: tuple[int, int] | None = None,
        stale_threshold: float = 30.0,
    ) -> dict[str, Any]:
        """카메라 하나를 교체 적용하고 실패 시 이전 캡처를 복구한다."""
        with self._camera_runtime_lock:
            old = self.camera_manager.get_camera(camera_id)
            old_args = None
            old_profile = self._camera_profiles.get(camera_id)
            if old is not None:
                old_args = {
                    "stream_url": old.stream_url,
                    "queue_size": old.queue_size,
                    "target_fps": old.target_fps,
                    "reconnect_interval": old.reconnect_interval,
                    "timeout": old.timeout,
                    "resize": old.resize,
                    "loop_file": old.loop_file,
                    "stale_threshold": old.stale_threshold,
                }
                self.remove_camera(camera_id)
            try:
                self.add_camera(
                    camera_id,
                    stream_url,
                    target_fps=target_fps,
                    resize=resize,
                    stale_threshold=stale_threshold,
                )
                if profile is not None:
                    self.set_camera_profile(camera_id, profile)
                return {"applied": True, "camera_id": camera_id}
            except Exception:
                if old_args is not None:
                    self.add_camera(camera_id, **old_args)
                    if old_profile is not None:
                        self.set_camera_profile(camera_id, old_profile)
                raise

    def get_camera_snapshot(self, camera_id: str) -> bytes | None:
        capture = self.camera_manager.get_camera(camera_id)
        frame = getattr(capture, "_latest_frame", None) if capture is not None else None
        if frame is None or frame.image is None:
            return None
        if self._snapshot_sanitizer_required and self._snapshot_sanitizer is None:
            logger.error("Snapshot blocked because the required privacy sanitizer is unavailable")
            return None
        image = frame.image
        if self._snapshot_sanitizer is not None:
            try:
                image = self._snapshot_sanitizer(image)
            except Exception as exc:
                logger.error("Snapshot privacy sanitization failed: %s", exc)
                return None
        ok, encoded = cv2.imencode(".jpg", image)
        return bytes(encoded) if ok else None

    def configure_snapshot_privacy(
        self,
        sanitizer: Callable[[np.ndarray], np.ndarray] | None,
        *,
        required: bool,
    ) -> None:
        self._snapshot_sanitizer = sanitizer
        self._snapshot_sanitizer_required = bool(required)

    def add_alert_callback(self, callback: Callable[[SafetyEvent], bool | None]):
        """알람 콜백 등록"""
        self.alert_callbacks.append(callback)

    def remove_alert_callback(self, callback: Callable[[SafetyEvent], bool | None]):
        """알람 콜백 제거(WS 수명주기 — 연결 해제 시 누수 방지)."""
        with contextlib.suppress(ValueError):
            self.alert_callbacks.remove(callback)

    def add_analysis_callback(self, callback: Callable[[SafetyEvent], None]):
        """알람이 아닌 VLM 관찰 이벤트 콜백 등록.

        COCO pretrained 모델처럼 safety 이벤트를 직접 만들 수 없는 경우에도
        person 탐지 프레임을 대시보드에 전달하고 VLM 분석을 수행하기 위해 사용한다.
        AlertManager에는 전달하지 않아 이메일/캡처 저장 알람 폭주를 방지한다.
        """
        self.analysis_callbacks.append(callback)

    def remove_analysis_callback(self, callback: Callable[[SafetyEvent], None]):
        """분석 콜백 제거(WS 수명주기 — 연결 해제 시 누수 방지)."""
        with contextlib.suppress(ValueError):
            self.analysis_callbacks.remove(callback)

    def start(self):
        """파이프라인 시작"""
        with self._camera_runtime_lock:
            self.is_running = True
            cameras = self.camera_manager.snapshot()
            for camera_id, _capture in cameras:
                self._camera_stop_events.setdefault(camera_id, threading.Event()).clear()
                self.frame_counters[camera_id] = 0
        # 활성 모델이 준비된 시점에 target_classes 도달 가능성 진단(1회).
        # COCO 모델에서 fire/smoke/fall 같은 산출 불가 클래스를 운영자가 지정한 경우 경고.
        try:
            self.validate_target_classes()
        except Exception as e:
            logger.debug(f"target_classes 검증 건너뜀: {e}")
        # Use the startup snapshot so concurrent apply/remove cannot mutate the
        # registry while worker threads are being created.
        for camera_id, capture in cameras:
            with self._camera_runtime_lock:
                stop_event = self._camera_stop_events.get(camera_id)
                if stop_event is None or self.camera_manager.get_camera(camera_id) is not capture:
                    continue
                # Start while holding the lifecycle lock. Otherwise an apply
                # can remove/stop this capture and this loop can resurrect it.
                capture.start()
                self._start_camera_thread(camera_id, capture, stop_event)
            logger.info(f"[{camera_id}] 처리 스레드 시작")

    def _process_camera(self, camera_id: str, capture: Any, stop_event: threading.Event):
        """카메라별 처리 루프"""
        while self.is_running and not stop_event.is_set():
            frame_obj = capture.get_frame(timeout=1.0)
            self._process_camera_health(camera_id, capture.get_status())
            if frame_obj is None:
                continue

            self._process_camera_tamper(camera_id, frame_obj.image)

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

    def _camera_event_enabled(self, camera_id: str, event_type: str) -> bool:
        release_qualified, _reason = release_qualification(event_type, "active")
        if not release_qualified:
            return False
        profile = self._camera_profiles.get(camera_id, {})
        if not isinstance(profile, dict):
            return False
        if "enabled_events" in profile:
            events = profile.get("enabled_events")
        else:
            events = profile.get("events")
        return isinstance(events, list) and event_type in events

    def _process_camera_health(self, camera_id: str, status: dict[str, Any]) -> None:
        transition = self.camera_health_monitor.update(camera_id, status)
        if transition is None or transition.event_type is None:
            return
        if not self._camera_event_enabled(camera_id, "camera_disconnect"):
            return

        is_recovery = transition.event_type == "camera_recovered"
        self._emit_runtime_event(
            camera_id=camera_id,
            event_type=transition.event_type,
            description=(
                "카메라 영상 수신이 복구되었습니다."
                if is_recovery
                else "카메라 영상이 설정된 제한 시간 이상 수신되지 않았습니다."
            ),
            alert_level="low" if is_recovery else "high",
            rule_name="camera_health_transition",
        )

    def _process_camera_tamper(self, camera_id: str, image: np.ndarray) -> None:
        if not self._camera_event_enabled(camera_id, "tamper"):
            return
        transition = self.tamper_monitor.update(camera_id, image)
        if transition is None:
            return
        is_recovery = transition.event_type == "tamper_recovered"
        descriptions = {
            "flat_dark": "화면이 장시간 어둡고 평탄해 렌즈 가림이 의심됩니다.",
            "flat_frame": "화면 정보가 장시간 사라져 카메라 조작이 의심됩니다.",
            "sharpness_collapse": "화면 선명도가 급격히 저하되어 초점 이탈이나 렌즈 가림이 의심됩니다.",
            "normal": "카메라 화면이 정상 상태로 복구되었습니다.",
        }
        self._emit_runtime_event(
            camera_id=camera_id,
            event_type=transition.event_type,
            description=descriptions.get(transition.reason, transition.reason),
            alert_level="low" if is_recovery else "high",
            rule_name="camera_tamper_monitor",
            image=None if is_recovery else image.copy(),
        )

    def _emit_runtime_event(
        self,
        camera_id: str,
        event_type: str,
        description: str,
        alert_level: str,
        rule_name: str,
        image: np.ndarray | None = None,
        detections: list[Detection] | None = None,
        confidence: float = 1.0,
        source_models: list[str] | None = None,
        timestamp: datetime | None = None,
        vlm_needed: bool = False,
        provisional_alert: bool = False,
        cooldown_sec: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        event = self._build_runtime_event(
            camera_id=camera_id,
            event_type=event_type,
            description=description,
            alert_level=alert_level,
            rule_name=rule_name,
            image=image,
            detections=detections,
            confidence=confidence,
            source_models=source_models,
            timestamp=timestamp,
            vlm_needed=vlm_needed,
            provisional_alert=provisional_alert,
            cooldown_sec=cooldown_sec,
            metadata=metadata,
        )
        self._publish_runtime_event(event)

    @staticmethod
    def _build_runtime_event(
        camera_id: str,
        event_type: str,
        description: str,
        alert_level: str,
        rule_name: str,
        image: np.ndarray | None = None,
        detections: list[Detection] | None = None,
        confidence: float = 1.0,
        source_models: list[str] | None = None,
        timestamp: datetime | None = None,
        vlm_needed: bool = False,
        provisional_alert: bool = False,
        cooldown_sec: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> SafetyEvent:
        return SafetyEvent(
            event_id=f"{camera_id}_{event_type}_{time.time_ns()}",
            camera_id=camera_id,
            event_type=event_type,
            timestamp=timestamp or datetime.now(),
            confidence=confidence,
            image=image,
            detections=list(detections or []),
            description=description,
            alert_level=alert_level,
            cooldown_sec=cooldown_sec,
            rule_name=rule_name,
            source_models=list(source_models or []),
            vlm_needed=vlm_needed,
            provisional_alert=provisional_alert,
            alert_state="provisional" if provisional_alert else "confirmed",
            metadata=dict(metadata or {}),
        )

    def _publish_runtime_event(self, event: SafetyEvent) -> None:
        self._record_event(event)
        self._fire_alert_callbacks(event, list(self.alert_callbacks))

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

        # 카메라 프로필 확인
        with self._camera_runtime_lock:
            profile = self._camera_profiles.get(camera_id, {})
            camera_generation = self._camera_generations.get(camera_id, 0)
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

        required_models = self.policy_engine.required_models(profile_name, enabled_events)
        required_models_unknown = not isinstance(required_models, list)
        if required_models_unknown:
            required_models = []
        if not required_models and not required_models_unknown:
            with self.latest_result_lock:
                self.latest_detections[camera_id] = []
            return

        # 카메라 ExecutionPlan에 필요한 모델만 실행한다.
        try:
            # The policy plan also names pose/action dependencies. detect_all is
            # intentionally detection-only, so never submit those models here.
            registry_models = getattr(self.model_registry, "models", None)
            detection_models = (
                [name for name in required_models if name in registry_models]
                if isinstance(registry_models, dict) else list(required_models)
            )
            all_detections = self.model_registry.detect_all(image, detection_models)
        except Exception as e:
            logger.error(f"[{camera_id}] 다중 모델 추론 실패: {e}")
            return
        if not self._is_current_camera_generation(camera_id, camera_generation):
            return

        with self.latest_result_lock:
            self.latest_detections[camera_id] = [
                detection
                for detections in all_detections.values()
                for detection in detections
            ]

        zones = profile.get("zones") if isinstance(profile, dict) else None
        if isinstance(zones, dict):
            height, width = image.shape[:2]
            zones = {
                zone_id: {
                    **zone,
                    "points": [
                        [float(point[0]) * width, float(point[1]) * height]
                        for point in zone.get("points", [])
                    ],
                    "normalized": False,
                }
                if isinstance(zone, dict) and zone.get("normalized") else zone
                for zone_id, zone in zones.items()
            }

        # Metadata-only observation aggregation. This path never stores frames,
        # invokes VLM, or emits alert callbacks.
        self.observation_store.record(
            camera_id=camera_id,
            timestamp=timestamp,
            detections_by_model=all_detections,
            zones=zones if isinstance(zones, dict) else None,
        )

        # Retain the current bounded JPEG before tracking can create a behavior event.
        self._remember_vlm_frame(camera_id, image, timestamp)

        try:
            self._process_tracking(
                camera_id=camera_id,
                timestamp=timestamp,
                image=image,
                all_detections=all_detections,
                zones=zones if isinstance(zones, dict) else {},
                enabled_events=enabled_events if isinstance(enabled_events, list) else None,
                profile_name=profile_name,
                camera_generation=camera_generation,
            )
        except Exception as exc:
            with self._state_lock:
                self._tracking_errors[camera_id] = (
                    self._tracking_errors.get(camera_id, 0) + 1
                )
            logger.error("[%s] tracking runtime failed: %s", camera_id, type(exc).__name__)
        if not self._is_current_camera_generation(camera_id, camera_generation):
            return

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
            if not self._is_current_camera_generation(camera_id, camera_generation):
                return
            self._dispatch_policy_event(decision.event)

    def _dispatch_policy_event(self, event: SafetyEvent) -> None:
        """Use the policy event lifecycle for detector and behavior candidates."""
        with self._event_dispatch_lock:
            cooldown_key = f"{event.camera_id}_{event.event_type}"
            now = time.time()
            cooldown = float(event.cooldown_sec)

            with self._state_lock:
                last = self.last_alerts.get(cooldown_key)
                if last is not None and now - last < cooldown:
                    return
                self.last_alerts[cooldown_key] = now

            self._record_event(event)

            logger.info(
                f"[{event.camera_id}] 정책 이벤트 발생: {event.event_type} "
                f"(alert_level={event.alert_level}, "
                f"confidence={event.confidence:.2f}, provisional={event.provisional_alert})"
            )

            # 생명안전 이벤트(provisional_alert=True)는 즉시 잠정 알림 발송
            # VLM은 이후 검증용으로 비동기 실행
            provisional = getattr(event, "provisional_alert", False)

            if provisional or event.verification_required:
                # 콜백 발행과 durable outbox 수락을 구분한다.
                event.provisional_published = True
                event.alert_state = "provisional"
                accepted = self._fire_alert_callbacks(event, list(self.alert_callbacks))
                if accepted and not event.provisional_sent:
                    event.provisional_sent = True
                    event.provisional_sent_at = datetime.now()

            if event.vlm_needed:
                # VLM 분석 제출 (비동기 검증)
                submitted = self._submit_vlm_analysis(
                    event,
                    alert_callbacks_after_vlm=list(self.alert_callbacks),
                )
                if not submitted:
                    # 제출 실패도 명시적 검토 상태로 기록하고 상태 변경을 발행한다.
                    if event.verification_state == "pending":
                        self._finalize_verification(event, "unverified", "SUBMIT_ERROR")
                    self._fire_alert_callbacks(event, list(self.alert_callbacks))
            elif provisional:
                # 정책상 VLM을 생략한 경우에도 잠정 상태를 무기한 남기지 않는다.
                # VLM was not required, so no verification revision is emitted.
                event.verification_state = "not_required"
            elif not provisional:
                # VLM 불필요 → 즉시 콜백 호출
                self._fire_alert_callbacks(event, list(self.alert_callbacks))

    def _process_tracking(
        self,
        camera_id: str,
        timestamp: datetime,
        image: np.ndarray,
        all_detections: dict[str, list[Detection]],
        zones: dict[str, Any],
        enabled_events: list[str] | None,
        profile_name: str,
        camera_generation: int | None = None,
    ) -> None:
        if not self._is_current_camera_generation(camera_id, camera_generation):
            return
        with self._tracking_state_lock:
            if (
                camera_generation is not None
                and self._camera_generations.get(camera_id, 0) != camera_generation
            ):
                return
            events, behavior_events = self._process_tracking_locked(
                camera_id=camera_id,
                timestamp=timestamp,
                image=image,
                all_detections=all_detections,
                zones=zones,
                enabled_events=enabled_events,
                profile_name=profile_name,
            )
        for event in events:
            with self._event_dispatch_lock:
                if not self._is_current_camera_generation(
                    camera_id, camera_generation
                ):
                    return
                self._publish_runtime_event(event)
        for event in behavior_events:
            if not self._is_current_camera_generation(camera_id, camera_generation):
                return
            self._dispatch_policy_event(event)

    def _process_tracking_locked(
        self,
        camera_id: str,
        timestamp: datetime,
        image: np.ndarray,
        all_detections: dict[str, list[Detection]],
        zones: dict[str, Any],
        enabled_events: list[str] | None,
        profile_name: str,
    ) -> tuple[list[SafetyEvent], list[SafetyEvent]]:
        events: list[SafetyEvent] = []
        behavior_events: list[SafetyEvent] = []
        selected = self.policy_engine.tracking_events_for_profile(profile_name, enabled_events)
        get_behavior_plans = getattr(self.policy_engine, "behavior_plans", None)
        behavior_plans = (
            get_behavior_plans(profile_name, enabled_events)
            if self._behavior_enabled and callable(get_behavior_plans) else []
        )
        if not isinstance(behavior_plans, list):
            behavior_plans = []
        if not isinstance(selected, list):
            selected = []
        if not selected and not behavior_plans:
            with self.latest_result_lock:
                self.latest_tracks[camera_id] = []
                self._latest_tracking_meta[camera_id] = {
                    "timestamp": timestamp,
                    "width": int(image.shape[1]),
                    "height": int(image.shape[0]),
                    "events": [],
                }
            return events, behavior_events

        union_detections: list[Detection] = []
        seen_keys: set[tuple[int, tuple[int, int, int, int], float]] = set()
        for event_name in selected:
            policy = self.policy_engine.effective_tracking_policy(profile_name, event_name, enabled_events)
            if policy is None:
                continue
            trigger = policy["trigger"]
            classes = {str(name) for name in trigger.get("classes", []) if name}
            min_confidence = float(trigger.get("min_confidence", 0.0))
            for model_name in trigger.get("models", []) or []:
                model_name = str(model_name)
                for detection in all_detections.get(model_name, []):
                    if detection.confidence < min_confidence:
                        continue
                    if classes and detection.class_name not in classes:
                        continue
                    key = (detection.class_id, detection.bbox, detection.confidence)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    union_detections.append(detection)

        # Behavior plans share this exact tracker update with tracking policies.
        for plan in behavior_plans:
            classes = {str(name) for name in plan.get("person_classes", []) if name}
            for model_name in plan.get("person_models", []):
                for detection in all_detections.get(str(model_name), []):
                    if classes and detection.class_name not in classes:
                        continue
                    key = (detection.class_id, detection.bbox, detection.confidence)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        union_detections.append(detection)

        tracks = self.track_manager.update(camera_id, union_detections, timestamp)
        with self.latest_result_lock:
            self.latest_tracks[camera_id] = tracks
            self._latest_tracking_meta[camera_id] = {
                "timestamp": timestamp,
                "width": int(image.shape[1]),
                "height": int(image.shape[0]),
                "events": list(selected),
            }

        if "line_crossing" in selected:
            policy = self.policy_engine.effective_tracking_policy(
                profile_name, "line_crossing", enabled_events
            )
            if policy is not None:
                events.extend(
                    self._process_line_crossing(
                        camera_id=camera_id,
                        timestamp=timestamp,
                        image=image,
                        tracks=self._tracks_for_policy(tracks, policy),
                        zones=zones,
                        policy=policy,
                    )
                )

        if "loitering" in selected:
            policy = self.policy_engine.effective_tracking_policy(
                profile_name, "loitering", enabled_events
            )
            if policy is not None:
                events.extend(
                    self._process_loitering(
                        camera_id=camera_id,
                        timestamp=timestamp,
                        image=image,
                        tracks=self._tracks_for_policy(tracks, policy),
                        zones=zones,
                        policy=policy,
                    )
                )
        for plan in behavior_plans:
            try:
                behavior_events.extend(self._process_behavior_plan(
                    camera_id, timestamp, image, tracks, all_detections, plan
                ))
            except Exception as exc:
                with self._state_lock:
                    self._behavior_counters["action_failures"] += 1
                logger.error("[%s] behavior runtime failed: %s", camera_id, type(exc).__name__)
        return events, behavior_events

    @staticmethod
    def _bbox_iou(left: tuple[float, float, float, float], right: tuple[float, float, float, float]) -> float:
        x1, y1 = max(left[0], right[0]), max(left[1], right[1])
        x2, y2 = min(left[2], right[2]), min(left[3], right[3])
        intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
        if not intersection:
            return 0.0
        left_area = max(0.0, left[2] - left[0]) * max(0.0, left[3] - left[1])
        right_area = max(0.0, right[2] - right[0]) * max(0.0, right[3] - right[1])
        return intersection / max(1e-9, left_area + right_area - intersection)

    def _associate_poses(
        self, observations: list[Any], tracks: list[TrackSnapshot]
    ) -> list[tuple[Any, TrackSnapshot]]:
        """Require an unambiguous one-to-one IoU assignment to a current track."""
        candidates: list[tuple[Any, TrackSnapshot]] = []
        claimed: dict[int, int] = {}
        for observation in observations:
            matches = [track for track in tracks if self._bbox_iou(observation.bbox, track.bbox) >= self._behavior_iou_threshold]
            if len(matches) != 1:
                with self._state_lock:
                    self._behavior_counters["ambiguous" if len(matches) > 1 else "unmatched"] += 1
                continue
            claimed[matches[0].track_id] = claimed.get(matches[0].track_id, 0) + 1
            candidates.append((observation, matches[0]))
        return [pair for pair in candidates if claimed[pair[1].track_id] == 1]

    def _process_behavior_plan(
        self,
        camera_id: str,
        timestamp: datetime,
        image: np.ndarray,
        tracks: list[TrackSnapshot],
        all_detections: dict[str, list[Detection]],
        plan: dict[str, Any],
    ) -> list[SafetyEvent]:
        pose_model, action_model = str(plan.get("pose_model", "")), str(plan.get("action_model", ""))
        if not pose_model or not action_model or self.model_registry is None:
            return []
        pose_detectors = getattr(self.model_registry, "pose_detectors", {})
        classifiers = getattr(self.model_registry, "action_classifiers", {})
        if pose_model not in pose_detectors or action_model not in classifiers:
            with self._state_lock:
                self._behavior_counters["unavailable"] += 1
            return []
        classes = set(plan.get("person_classes", []) or [])
        boxes = [d.bbox for model in plan.get("person_models", []) for d in all_detections.get(model, []) if not classes or d.class_name in classes]
        try:
            observations = self.model_registry.detect_poses(image, boxes, pose_model, timestamp.timestamp())
        except Exception:
            with self._state_lock:
                self._behavior_counters["pose_failures"] += 1
            return []
        classifier = classifiers[action_model]
        sequence_length = int(getattr(classifier, "sequence_length", 0))
        if sequence_length <= 0:
            return []
        buffer = self._behavior_buffers.setdefault(action_model, BehaviorBuffer(
            sequence_length,
            stale_after_sec=float(self.behavior_settings.get("stale_after_sec", 10.0)),
            max_tracks_per_camera=int(self.behavior_settings.get("max_tracks_per_camera", 100)),
        ))
        buffer.prune_stale(timestamp.timestamp())
        events: list[SafetyEvent] = []
        for observation, track in self._associate_poses(observations, [t for t in tracks if t.class_name == "person"]):
            if not buffer.append(camera_id, track.track_id, observation):
                continue
            sequence = buffer.sequence(camera_id, track.track_id)
            tick_key = (camera_id, track.track_id, action_model)
            self._behavior_ticks[tick_key] = self._behavior_ticks.get(tick_key, 0) + 1
            if sequence is None or self._behavior_ticks[tick_key] % self._behavior_stride:
                continue
            try:
                prediction = self.model_registry.classify_action(sequence, action_model)
            except Exception:
                with self._state_lock:
                    self._behavior_counters["action_failures"] += 1
                continue
            with self._state_lock:
                self._behavior_counters["inferences"] += 1
            if prediction is None or prediction.action != plan.get("action_label") or prediction.confidence < float(plan.get("min_confidence", 0.0)):
                continue
            detection = Detection(-1, prediction.action, prediction.confidence, track.bbox)
            rule = {"type": "direct_detection", "classes": [prediction.action], "temporal": plan.get("temporal", {})}
            result = self.policy_engine.rule_evaluators.evaluate(
                rule, {action_model: [detection]}, image, camera_id=camera_id, policy_name=plan["event_name"]
            )
            if not result.get("triggered"):
                continue
            metadata = {
                "track_id": track.track_id, "action": prediction.action,
                "confidence": prediction.confidence, "model_names": [pose_model, action_model],
                "sequence_length": sequence_length, "first_seen": track.first_seen.isoformat(),
                "last_seen": track.last_seen.isoformat(),
            }
            event = self._build_runtime_event(
                camera_id, plan["event_name"], plan.get("description", ""), plan["alert_level"],
                "stgcn_action", image.copy(), [detection], prediction.confidence,
                [pose_model, action_model], timestamp, plan["vlm"] != "never",
                plan["provisional_alert"], plan["cooldown_sec"], metadata,
            )
            if event.vlm_needed:
                verification = plan.get("verification", {})
                event.verification_required = True
                event.verification_mode = str(verification.get("mode", "single"))
                event.verification_quorum = int(verification.get("quorum", 1))
                event.verification_state = "pending"
                event.alert_state = "provisional"
                event.metadata["verification_allow_reject"] = bool(verification.get("allow_reject", False))
            events.append(event)
        return events

    def _process_line_crossing(
        self,
        camera_id: str,
        timestamp: datetime,
        image: np.ndarray,
        tracks: list[TrackSnapshot],
        zones: dict[str, Any],
        policy: dict[str, Any],
    ) -> list[SafetyEvent]:
        events: list[SafetyEvent] = []
        rule = policy.get("rule", {}) or {}
        zone_id = str(rule.get("zone_id", "line"))
        zone = zones.get(zone_id)
        selected_zones = {zone_id: zone} if isinstance(zone, dict) else {}
        candidates = self.line_crossing_monitor.evaluate(
            camera_id,
            tracks,
            selected_zones,
            timestamp,
            cooldown_sec=policy["cooldown_sec"],
            min_displacement=float(rule.get("min_displacement_px", 0.0)),
        )
        rule_name = policy["rule_name"]
        source_models = policy["source_models"]
        for candidate in candidates:
            track = candidate.track
            detection = Detection(
                class_id=-1,
                class_name=track.class_name,
                confidence=track.confidence,
                bbox=track.bbox,
            )
            direction_label = self._line_crossing_direction_label(candidate)
            # Persist decision facts only. The event detection already carries the
            # triggering bbox; full trajectories and zone geometry remain ephemeral.
            metadata = {
                "track_id": track.track_id,
                "class_name": track.class_name,
                "zone_id": candidate.zone_id,
                "direction": candidate.direction,
                "configured_direction": candidate.configured_direction,
                "direction_label": direction_label,
                "first_seen": track.first_seen.isoformat(),
                "last_seen": track.last_seen.isoformat(),
                "policy_cooldown_sec": candidate.cooldown_sec,
            }
            events.append(
                self._build_runtime_event(
                    camera_id=camera_id,
                    event_type="line_crossing",
                    description=(
                        f"{track.class_name} 객체가 {candidate.zone_id} 감지선을 "
                        f"{direction_label} 방향으로 통과했습니다. "
                        f"track_id={track.track_id}"
                    ),
                    alert_level=policy["alert_level"],
                    rule_name=rule_name,
                    image=image.copy() if image is not None else None,
                    detections=[detection],
                    confidence=track.confidence,
                    source_models=source_models,
                    timestamp=timestamp,
                    vlm_needed=policy["vlm"] != "never",
                    provisional_alert=policy["provisional_alert"],
                    cooldown_sec=policy["cooldown_sec"],
                    metadata=metadata,
                )
            )
        return events

    def _process_loitering(
        self,
        camera_id: str,
        timestamp: datetime,
        image: np.ndarray,
        tracks: list[TrackSnapshot],
        zones: dict[str, Any],
        policy: dict[str, Any],
    ) -> list[SafetyEvent]:
        events: list[SafetyEvent] = []
        candidates = self.loitering_monitor.evaluate(
            camera_id=camera_id,
            tracks=tracks,
            zones=zones,
            timestamp=timestamp,
            policy=policy,
        )
        rule = policy.get("rule", {}) or {}
        rule_name = policy["rule_name"]
        source_models = policy["source_models"]
        for candidate in candidates:
            track = candidate.track
            detection = Detection(
                class_id=-1,
                class_name=track.class_name,
                confidence=track.confidence,
                bbox=track.bbox,
            )
            # Persist decision facts only. The event detection already carries the
            # triggering bbox; full trajectories and zone geometry remain ephemeral.
            metadata = {
                "track_id": track.track_id,
                "class_name": track.class_name,
                "zone_id": candidate.zone_id,
                "zone_type": "polygon",
                "first_in_zone": candidate.first_in_zone.isoformat(),
                "event_timestamp": candidate.timestamp.isoformat(),
                "dwell_sec": candidate.dwell_sec,
                "grace_sec": float(rule.get("grace_sec", 3.0)),
                "observed_samples": candidate.observed_samples,
                "max_confidence": candidate.max_confidence,
                "policy_cooldown_sec": policy["cooldown_sec"],
            }
            events.append(self._build_runtime_event(
                camera_id=camera_id,
                event_type="loitering",
                description=(
                    f"{track.class_name} 객체가 {candidate.zone_id} 구역에 "
                    f"{candidate.dwell_sec:.0f}초 이상 체류했습니다. track_id={track.track_id}"
                ),
                alert_level=policy["alert_level"],
                rule_name=rule_name,
                image=image.copy() if image is not None else None,
                detections=[detection],
                confidence=track.confidence,
                source_models=source_models,
                timestamp=timestamp,
                vlm_needed=policy["vlm"] != "never",
                provisional_alert=policy["provisional_alert"],
                cooldown_sec=policy["cooldown_sec"],
                metadata=metadata,
            ))
        return events

    @staticmethod
    def _line_crossing_direction_label(candidate: LineCrossingCandidate) -> str:
        if candidate.configured_direction != "any":
            return "화살표"
        return {
            "positive": "기준 화살표",
            "negative": "기준 화살표 반대",
        }.get(candidate.direction, candidate.direction)

    @staticmethod
    def _tracks_for_policy(
        tracks: list[TrackSnapshot],
        policy: dict[str, Any],
    ) -> list[TrackSnapshot]:
        trigger = policy.get("trigger", {}) or {}
        classes = {str(name) for name in trigger.get("classes", []) if name}
        min_confidence = float(trigger.get("min_confidence", 0.0))
        return [
            track
            for track in tracks
            if track.confidence >= min_confidence
            and (not classes or track.class_name in classes)
        ]

    def _is_current_camera_generation(
        self,
        camera_id: str,
        generation: int | None,
    ) -> bool:
        if generation is None:
            return True
        with self._camera_runtime_lock:
            return self._camera_generations.get(camera_id, 0) == generation

    def _process_frame_legacy(self, camera_id: str, image: np.ndarray, timestamp: datetime):
        """기존 단일 detector 모드 (하위 호환, deprecated).

        운영 데몬은 policy-mode를 사용한다. 이 경로는 테스트·호환용으로만 유지되며
        향후 명시적 opt-in으로 전환될 예정이다.
        """
        if not self._legacy_warned:
            self._legacy_warned = True
            logger.warning(
                "legacy 단일-detector 경로 사용 중 — policy-mode 이관을 권장한다."
            )
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
                # detect_safety_gear 반환 dict의 값이 느슨하게 타입되어 mypy가 object로 보지만
                # "violations"는 list[Detection]임이 보장된다.
                detections=safety_check["violations"],  # type: ignore[arg-type]
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
            tracking_errors = dict(self._tracking_errors)
            behavior = self._behavior_status()

        camera_status = self.camera_manager.get_status()
        with self.latest_result_lock:
            track_counts = {
                camera_id: len(tracks) for camera_id, tracks in self.latest_tracks.items()
            }
        for camera_id, status in camera_status.items():
            status["health_state"] = self.camera_health_monitor.state(camera_id).value
            status["tamper_state"] = self.tamper_monitor.state(camera_id)
            status["active_tracks"] = track_counts.get(camera_id, 0)
            get_tracker_stats = getattr(self.track_manager, "stats", None)
            tracker_stats = get_tracker_stats(camera_id) if callable(get_tracker_stats) else {}
            if not isinstance(tracker_stats, dict):
                tracker_stats = {}
            status["tracking"] = {
                **tracker_stats,
                "active_tracks": track_counts.get(camera_id, 0),
                "runtime_errors": tracking_errors.get(camera_id, 0),
                "line_crossing": self.line_crossing_monitor.stats(camera_id),
                "loitering": self.loitering_monitor.stats(camera_id),
            }
            status["behavior"] = self._behavior_status(camera_id)

        return {
            "running": self.is_running,
            "cameras": camera_status,
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
            "behavior": behavior,
            "ai_runtime": self.get_ai_runtime_status(),
        }

    def _behavior_status(self, camera_id: str | None = None) -> dict[str, Any]:
        return {
            "enabled": self._behavior_enabled,
            "iou_threshold": self._behavior_iou_threshold,
            "inference_stride": self._behavior_stride,
            "counters": dict(self._behavior_counters),
            "buffers": {
                name: buffer.status(camera_id)
                for name, buffer in self._behavior_buffers.items()
            },
        }

    def get_latest_detections(self, camera_id: str) -> list[Detection]:
        """카메라별 최신 탐지 결과 조회"""
        with self.latest_result_lock:
            return list(self.latest_detections.get(camera_id, []))

    def get_observations(self, camera_id: str | None = None, limit: int = 120) -> list[dict[str, Any]]:
        return self.observation_store.query(camera_id=camera_id, limit=limit)

    def get_latest_tracks(self, camera_id: str) -> list[TrackSnapshot]:
        with self.latest_result_lock:
            return list(self.latest_tracks.get(camera_id, []))

    def get_tracking_snapshot(self, camera_id: str) -> dict[str, Any]:
        """Return bounded, normalized tracking data for an operator overlay."""
        with self._camera_runtime_lock:
            profile = dict(self._camera_profiles.get(camera_id, {}))
        with self.latest_result_lock:
            tracks = list(self.latest_tracks.get(camera_id, []))
            meta = dict(self._latest_tracking_meta.get(camera_id, {}))

        width = max(1, int(meta.get("width", 1)))
        height = max(1, int(meta.get("height", 1)))
        timestamp = meta.get("timestamp")
        if isinstance(timestamp, datetime):
            now = datetime.now(timestamp.tzinfo) if timestamp.tzinfo else datetime.now()
            age_sec = max(0.0, (now - timestamp).total_seconds())
            timestamp_value = timestamp.isoformat()
        else:
            age_sec = None
            timestamp_value = None

        def point(x: float, y: float) -> list[float]:
            return [
                min(1.0, max(0.0, float(x) / width)),
                min(1.0, max(0.0, float(y) / height)),
            ]

        track_values = []
        for track in tracks:
            x1, y1, x2, y2 = track.bbox
            track_values.append({
                "track_id": track.track_id,
                "class_name": track.class_name,
                "confidence": track.confidence,
                "bbox": [*point(x1, y1), *point(x2, y2)],
                "trajectory": [
                    point(item.x, item.y) for item in track.trajectory[-20:]
                ],
                "first_seen": track.first_seen.isoformat(),
                "last_seen": track.last_seen.isoformat(),
            })

        zone_values = []
        zones = profile.get("zones", {}) if isinstance(profile, dict) else {}
        if isinstance(zones, dict):
            for zone_id, zone in zones.items():
                if not isinstance(zone, dict):
                    continue
                raw_points = zone.get("points", [])
                normalized = bool(zone.get("normalized"))
                zone_points = [
                    [float(item[0]), float(item[1])]
                    if normalized
                    else point(float(item[0]), float(item[1]))
                    for item in raw_points
                    if isinstance(item, (list, tuple)) and len(item) >= 2
                ]
                zone_values.append({
                    "id": str(zone_id),
                    "type": str(zone.get("type", "polygon")),
                    "points": zone_points,
                    "direction": str(zone.get("direction", "any")),
                })

        return {
            "camera_id": camera_id,
            "timestamp": timestamp_value,
            "age_sec": age_sec,
            "stale": age_sec is None or age_sec > 3.0,
            "frame": {"width": width, "height": height},
            "events": list(meta.get("events", [])),
            "tracks": track_values,
            "zones": zone_values,
        }
