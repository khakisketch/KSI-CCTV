"""KSI-CCTV 헤드리스 무인 탐지 러너 — systemd/Docker ExecStart 진입점.

대시보드 "▶️ 시작" 버튼 없이 파이프라인을 자동 기동하는 24시간 무인 운영용 데몬이다.
구성: 설정 로드 → MultiModelRegistry + EventPolicyEngine + SafetyPipeline + AlertManager
조립 → 카메라 등록 → /health 엔드포인트에 **실 파이프라인 상태**(카메라/VLM/모델 health) 연결
→ pipeline.start() → SIGTERM/SIGINT까지 블록.

웹 콘솔(frontend/, Next.js)은 별도 컨테이너의 운영자 도구로, 이 데몬의 엔진 API를 호출한다.
이 데몬이 탐지·알람(이메일/로그/캡처)·엔진 API·헬스를 책임진다.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

sys.path.append(str(Path(__file__).parent.parent))

from src.alerts.alert_manager import AlertConfig, AlertManager
from src.cameras import CameraRepository, CredentialStore, MediaMTXClient, MediaMTXError, RtspProbe
from src.detection.model_registry import MultiModelRegistry
from src.events.verification_reconcile import reconcile_pending_verifications
from src.incidents import IncidentStore
from src.pipeline.pipeline import SafetyEvent, SafetyPipeline
from src.policy.event_policy_engine import EventPolicyEngine
from src.storage import SQLiteEventRepository, SQLiteObservationStore
from src.tracking import ByteTrackManager
from src.utils.config import AppConfig, ConfigLoader
from src.utils.health import (
    HealthChecker,
    check_disk_space,
    check_memory_usage,
    register_pipeline_health_checks,
)
from src.vlm.vlm_inference import create_vlm_provider

logger = logging.getLogger("ksi_cctv.run")

# 종료 신호 수신 시 set되어 메인 루프를 깨운다.
_shutdown = threading.Event()

_API_START_TIMEOUT_SECONDS = 10.0
_API_JOIN_TIMEOUT_SECONDS = 5.0
_API_MONITOR_INTERVAL_SECONDS = 1.0


def _sd_notify(state: str) -> None:
    """systemd sd_notify 전송 — NOTIFY_SOCKET 미설정(비-systemd: 테스트/Docker/수동실행)이면 no-op.

    READY=1(기동완료), WATCHDOG=1(생존 ping), STOPPING=1(정상종료)을 systemd에 알린다.
    실패해도 데몬 동작에 영향 없도록 조용히 무시한다(watchdog ping 실패 1회는 다음 ping이 복구).
    """
    addr = os.environ.get("NOTIFY_SOCKET")
    if not addr:
        return
    try:
        import socket

        # 추상 네임스페이스 소켓(@)은 첫 바이트가 NUL
        sock_addr = "\0" + addr[1:] if addr.startswith("@") else addr
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.connect(sock_addr)
            sock.sendall(state.encode())
    except Exception as e:  # pragma: no cover - 환경 의존
        logger.debug("sd_notify(%s) 실패(무시): %s", state, e)


def _camera_policy_profile(
    camera: dict[str, Any], profiles_config: dict[str, Any]
) -> dict[str, Any]:
    """카메라 정책 profile 생성(헤드리스 — 대시보드 session_state override 없음).

    우선순위: camera_profiles.yaml의 cameras.<id> 설정 → cameras.json의 detection_type → 기본값.
    enabled_events가 None이면 EventPolicyEngine이 "필터 없음"으로 해석한다(빈 리스트=전부 비활성과 구분).
    """
    camera_id = camera.get("id", "")
    configured = (
        profiles_config.get("cameras", {}).get(camera_id, {})
        if isinstance(profiles_config, dict)
        else {}
    )
    domain = configured.get("domain") or camera.get("detection_type") or "construction"
    events = configured.get("enabled_events")  # None=필터없음
    zones = configured.get("zones") or {}
    detection_type = camera.get("detection_type") or domain
    return {
        "domain": domain,
        "events": events,
        "enabled_events": events,
        "zones": zones,
        "detection_type": detection_type,
    }


def build_pipeline(config: AppConfig) -> tuple[SafetyPipeline, AlertManager, MultiModelRegistry]:
    """설정으로 policy-mode 파이프라인 + 알람관리자를 조립한다(카메라 등록 전).

    활성 모델이 없으면 RuntimeError. (테스트/검증에서 재사용 가능하도록 분리.)
    """
    # Signed Appliance releases must not silently omit an enabled safety model.
    required_models = os.environ.get("KSI_CCTV_REQUIRE_ALL_MODELS", "").lower() in {"1", "true", "yes"}
    required_gpu = os.environ.get("KSI_CCTV_REQUIRE_GPU", "").lower() in {"1", "true", "yes"}
    registry = MultiModelRegistry.from_config(
        "./config/model_profiles.yaml", strict=required_models, require_gpu=required_gpu
    )
    engine = EventPolicyEngine.from_config("./config/event_policies.yaml")
    if not registry.active_model_names:
        raise RuntimeError(
            f"활성 모델이 없어 기동할 수 없습니다. failed_models={registry.failed_models}"
        )
    if registry.failed_models:
        logger.warning("일부 모델 로드 실패: %s", registry.failed_models)

    primary_detector = registry.get_model("general_yolo") or next(
        iter(registry.models.values()), None
    )

    det_cfg = config.get_detection_config()
    vlm_cfg = config.get("models", "vlm", default={}) or {}
    target_classes = det_cfg.get("target_classes", "person,fire,smoke,fall")
    if isinstance(target_classes, str):
        target_classes = [s.strip() for s in target_classes.split(",") if s.strip()]
    vlm_trigger = det_cfg.get("vlm_trigger_classes", "")
    if isinstance(vlm_trigger, str):
        vlm_trigger = [s.strip() for s in vlm_trigger.split(",") if s.strip()]
    camera_cfg = config.get("cameras", default={}) or {}
    storage_cfg = config.get("storage", default={}) or {}
    observation_path = os.environ.get(
        "KSI_CCTV_OBSERVATIONS_DB",
        storage_cfg.get("observations_db", "./data/observations.db"),
    )
    observation_store = SQLiteObservationStore(
        observation_path,
        max_buckets_per_camera=int(
            storage_cfg.get("observation_max_buckets_per_camera", 43200)
        ),
        retention_days=int(storage_cfg.get("observation_retention_days", 30)),
    )
    event_path = os.environ.get(
        "KSI_CCTV_EVENTS_DB",
        storage_cfg.get("events_db", observation_path),
    )
    event_repository = SQLiteEventRepository(
        event_path,
        retention_days=int(storage_cfg.get("event_retention_days", 30)),
    )
    # 하드 크래시 잔여분: durable 저장소에 pending으로 남은 VLM 검증을
    # 시작 시 unverified(RESTART_LOST)로 확정한다(fail-open 보존, 억제 금지).
    # graceful stop 잔여분은 파이프라인이 이미 FORCED_SHUTDOWN으로 확정한다.
    reconciled = reconcile_pending_verifications(event_repository)
    if reconciled > 0:
        logger.info("미확정 VLM 검증 %d건을 unverified(RESTART_LOST)로 확정.", reconciled)
    incident_store = IncidentStore(event_path)
    incident_store.validate_schema()
    tracking_cfg = config.get("tracking", default={}) or {}
    behavior_cfg = config.get("behavior", default={}) or {}
    track_manager = ByteTrackManager(
        track_high_thresh=float(tracking_cfg.get("track_high_thresh", 0.35)),
        track_low_thresh=float(tracking_cfg.get("track_low_thresh", 0.10)),
        new_track_thresh=float(tracking_cfg.get("new_track_thresh", 0.40)),
        track_buffer=int(tracking_cfg.get("track_buffer", 30)),
        match_thresh=float(tracking_cfg.get("match_thresh", 0.80)),
        trajectory_size=int(tracking_cfg.get("trajectory_size", 120)),
        stale_history_sec=float(tracking_cfg.get("stale_history_sec", 300.0)),
    )

    pipeline = SafetyPipeline(
        detector=primary_detector,
        skip_frames=int(det_cfg.get("skip_frames", 5)),
        alert_cooldown=float(det_cfg.get("alert_cooldown_sec", 30.0)),
        target_classes=target_classes,
        vlm_trigger_classes=vlm_trigger,
        vlm_cooldown=float(det_cfg.get("vlm_cooldown_sec", 60.0)),
        model_registry=registry,
        policy_engine=engine,
        camera_stale_after_sec=float(camera_cfg.get("health_stale_after_sec", 10.0)),
        camera_disconnect_after_sec=float(camera_cfg.get("disconnect_after_sec", 30.0)),
        tamper_duration_sec=float(camera_cfg.get("tamper_duration_sec", 5.0)),
        tamper_recovery_sec=float(camera_cfg.get("tamper_recovery_sec", 3.0)),
        observation_store=observation_store,
        track_manager=track_manager,
        behavior_settings=behavior_cfg,
        vlm_queue_max_in_flight=int(vlm_cfg.get("queue_soft_limit", 8)),
        vlm_queue_hard_limit=int(vlm_cfg.get("queue_hard_limit", 16)),
        vlm_frame_max_frames=int(vlm_cfg.get("max_frames", 3)),
        vlm_frame_max_dimension=int(vlm_cfg.get("max_dimension", 640)),
        vlm_frame_jpeg_quality=int(vlm_cfg.get("jpeg_quality", 80)),
        vlm_frame_total_bytes=int(vlm_cfg.get("max_total_bytes", 3145728)),
        vlm_frame_window_sec=float(vlm_cfg.get("frame_window_sec", 5)),
    )

    alerts_cfg = config.get_alert_config()
    # R9 C-1: 데몬 경로 AlertManager에 실제 엔드포인트 리더를 주입한다.
    # 미주입(None)이면 _get_webhook_endpoints()가 항상 []라 실환경 webhook 영구 스킵.
    # 함수-로컬 import — service가 alert_manager를 참조하므로 모듈-로드 시점 순환을 피한다
    # (직접 주입 검증됨: `import src.run` + `from src.api.routes.webhooks import ...` 순환 없음).
    from src.api.routes.webhooks import load_webhook_endpoints

    alert_manager = AlertManager(AlertConfig(
        enabled=bool(alerts_cfg.get("enabled", True)),
        log_file=alerts_cfg.get("log_file", "./logs/alerts.log"),
        console_output=bool(alerts_cfg.get("console_output", True)),
        save_images=bool(alerts_cfg.get("save_images", True)),
        image_dir=alerts_cfg.get("image_dir", "./data/captures"),
        image_retention_days=int(alerts_cfg.get("image_retention_days", 30)),
        blur_faces=bool(alerts_cfg.get("blur_faces", True)),
        face_blur_strength=int(alerts_cfg.get("face_blur_strength", 35)),
        log_max_bytes=int(alerts_cfg.get("log_max_bytes", 10 * 1024 * 1024)),
        log_backup_count=int(alerts_cfg.get("log_backup_count", 5)),
        encrypt_images=bool(alerts_cfg.get("encrypt_images", True)),
        outbox_dir=alerts_cfg.get("outbox_dir", "./data/outbox"),
    ), event_repository=event_repository, incident_store=incident_store,
       webhook_endpoint_provider=load_webhook_endpoints)
    pipeline.configure_snapshot_privacy(
        alert_manager.prepare_image_for_display,
        required=alert_manager.config.blur_faces,
    )

    def on_alert(event: SafetyEvent) -> bool:
        return alert_manager.handle_event(event)

    pipeline.add_alert_callback(on_alert)

    # VLM 활성화 (설정 시) — provider 팩토리로 config의 provider 키를 따라 생성(선택적 컴포넌트).
    configured_vlm = bool(vlm_cfg.get("enabled", False))
    digest = str(vlm_cfg.get("model_digest", "")).lower()
    qualified_vlm = bool(vlm_cfg.get("runtime_qualified", False)) and len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )
    pipeline.set_vlm_runtime_intent(
        configured_enabled=configured_vlm,
        runtime_qualified=qualified_vlm,
        provider_available=False if configured_vlm else None,
        reason_category="pending_startup" if configured_vlm else "disabled",
    )
    provider = create_vlm_provider(vlm_cfg)
    if provider is not None:
        pipeline.enable_vlm(
            provider,
            vlm_cfg.get("prompt_template", "./config/prompts/construction.yaml"),
            max_concurrent=vlm_cfg.get("max_concurrent", 2),
        )
        logger.info(
            "VLM 활성화: provider=%s model=%s",
            vlm_cfg.get("provider", "ollama"),
            vlm_cfg.get("model_name", "qwen3-vl:latest"),
        )
    else:
        pipeline.set_vlm_runtime_intent(
            provider_available=False if configured_vlm else None,
            reason_category="unqualified" if configured_vlm and not qualified_vlm else (
                "provider_unavailable" if configured_vlm else "disabled"
            ),
        )
        logger.info("VLM 비활성(2차 정밀분석 없음) — 1차 YOLO 탐지만 수행.")

    return pipeline, alert_manager, registry


def _register_cameras(
    pipeline: SafetyPipeline,
    config: AppConfig,
    camera_repository: CameraRepository | None = None,
    camera_credentials: CredentialStore | None = None,
    rtsp_probe: RtspProbe | None = None,
    mediamtx: MediaMTXClient | None = None,
) -> int:
    """config의 활성 카메라를 파이프라인에 등록. 등록된 카메라 수 반환."""
    cameras_by_id = {
        str(camera.get("id")): camera
        for camera in config.load_cameras()
        if camera.get("id")
    }
    # Managed 저장소 출처 ID. 이 카메라는 위 managed 루프가 게이트웨이 sync를
    # 전담하므로 최종 등록 루프에서 중복 sync하지 않는다.
    managed_ids: set[str] = set()
    if camera_repository is not None:
        _, managed = camera_repository.list()
        probe = rtsp_probe or RtspProbe(
            allow_loopback=os.environ.get("KSI_CCTV_ALLOW_LOOPBACK_CAMERA", "").lower()
            in {"1", "true", "yes"}
        )
        for camera in managed:
            parsed = urlsplit(camera.rtsp_url)
            try:
                addresses = probe.allowed_host(parsed.hostname or "", parsed.port or 554)
            except OSError:
                addresses = []
            if not addresses:
                logger.error("[%s] RTSP target is not a private LAN address; skipping.", camera.id)
                continue
            credential = camera_credentials.get(camera.credential_ref) if camera_credentials else None
            target_scope = CredentialStore.target_scope(addresses, parsed.port or 554)
            stored_scope = (credential or {}).get("target_scope")
            if stored_scope and stored_scope != target_scope:
                logger.error("[%s] credential target changed; skipping until revalidated.", camera.id)
                continue
            if credential and not stored_scope:
                logger.error("[%s] credential target is unverified; re-entry required.", camera.id)
                continue
            cameras_by_id[camera.id] = {
                "id": camera.id,
                "name": camera.name,
                "location": camera.location,
                # Never persist this rewritten URL; pinning prevents a DNS
                # answer from changing between validation and capture startup.
                "stream_url": RtspProbe.pin_url(
                    RtspProbe.with_credentials(
                        camera.rtsp_url,
                        (credential or {}).get("username"),
                        (credential or {}).get("password"),
                    ),
                    addresses[0],
                ),
                "fps": camera.fps,
                "resolution": camera.resolution,
                "enabled": camera.enabled,
                "detection_type": camera.profile or "general",
                "managed_profile": {
                    "domain": camera.profile or "general",
                    "enabled_events": list(camera.enabled_events),
                    "events": list(camera.enabled_events),
                    "zones": {
                        zone.id: {
                            "type": zone.type,
                            "points": [[point.x, point.y] for point in zone.points],
                            "normalized": True,
                            "direction": zone.direction,
                        }
                        for zone in camera.zones
                    },
                    "detection_type": camera.profile or "general",
                },
            }
            if camera.enabled and mediamtx is not None:
                try:
                    mediamtx.sync(camera.id, cameras_by_id[camera.id]["stream_url"])
                except MediaMTXError as exc:
                    logger.error("[%s] MediaMTX path sync failed: %s", camera.id, type(exc).__name__)
                except Exception as exc:
                    logger.error("[%s] MediaMTX path sync failed: %s", camera.id, type(exc).__name__)
            managed_ids.add(camera.id)
    cameras = sorted(
        (camera for camera in cameras_by_id.values() if camera.get("enabled")),
        key=lambda camera: str(camera.get("id", "")),
    )
    if len(cameras) > CameraRepository.MAX_CAMERAS:
        skipped = [str(camera.get("id", "unknown")) for camera in cameras[CameraRepository.MAX_CAMERAS:]]
        logger.error(
            "활성 카메라가 제품 한도 %d대를 초과해 등록하지 않습니다: %s",
            CameraRepository.MAX_CAMERAS,
            ", ".join(skipped),
        )
        cameras = cameras[:CameraRepository.MAX_CAMERAS]
    profiles = ConfigLoader.load("./config/camera_profiles.yaml")
    # stale 판정 임계(초) — config 기반(이전엔 코드 고정 30s라 현장 조정 불가였음).
    stale_threshold = float(config.get("cameras", "stale_threshold_sec", default=30.0))
    registered = 0
    for cam in cameras:
        cam_id = cam.get("id", "unknown")
        stream_url = cam.get("stream_url", "")
        # os.path.expandvars 후에도 "${"가 남으면 env 미주입 — 해당 카메라만 건너뛴다(데몬은 계속).
        if not stream_url or "${" in stream_url:
            logger.error("[%s] stream_url 환경변수 미설정 — 이 카메라를 건너뜁니다.", cam_id)
            continue
        pipeline.set_camera_profile(
            cam_id,
            cam.get("managed_profile") or _camera_policy_profile(cam, profiles),
        )
        pipeline.add_camera(
            cam_id, stream_url,
            target_fps=int(cam.get("fps", 30)),
            resize=cam.get("resolution"),
            stale_threshold=stale_threshold,
        )
        # Legacy(cameras.json) 카메라도 managed와 동일하게 게이트웨이에 sync한다.
        # Managed 출처는 위 managed 루프가 전담하므로 제외한다. 로컬 파일 입력은
        # 파이프라인 내부 소스라 게이트웨이 대상에서 제외한다.
        if (
            mediamtx is not None
            and cam_id not in managed_ids
            and stream_url.lower().startswith(("rtsp://", "rtsps://"))
        ):
            try:
                mediamtx.sync(cam_id, stream_url)
            except MediaMTXError as exc:
                logger.error("[%s] MediaMTX path sync failed: %s", cam_id, type(exc).__name__)
            except Exception as exc:
                logger.error("[%s] MediaMTX path sync failed: %s", cam_id, type(exc).__name__)
        registered += 1
    return registered


def _reconcile_managed_gateway(
    camera_repository: CameraRepository,
    camera_credentials: CredentialStore,
    probe: RtspProbe,
    mediamtx: MediaMTXClient,
    camera_runtime_lock: threading.RLock,
) -> None:
    """Recreate ephemeral MediaMTX paths after gateway restarts."""
    _, cameras = camera_repository.list()
    for snapshot in cameras:
        with camera_runtime_lock:
            try:
                # API mutations use the same lock. Re-read after waiting so a
                # stale periodic snapshot can never overwrite a newer source.
                camera = camera_repository.get(snapshot.id)
                if camera is None or not camera.enabled:
                    mediamtx.delete(snapshot.id)
                    continue
                parsed = urlsplit(camera.rtsp_url)
                addresses = probe.allowed_host(parsed.hostname or "", parsed.port or 554)
                if not addresses:
                    logger.error("[%s] gateway reconcile denied non-LAN RTSP target", camera.id)
                    continue
                credential = camera_credentials.get(camera.credential_ref) or {}
                target_scope = CredentialStore.target_scope(addresses, parsed.port or 554)
                stored_scope = credential.get("target_scope")
                if stored_scope and stored_scope != target_scope:
                    logger.error(
                        "[%s] credential target changed; gateway reconcile skipped",
                        camera.id,
                    )
                    continue
                if credential and not stored_scope:
                    logger.error(
                        "[%s] credential target is unverified; gateway reconcile skipped",
                        camera.id,
                    )
                    continue
                stream_url = RtspProbe.pin_url(
                    RtspProbe.with_credentials(
                        camera.rtsp_url,
                        credential.get("username"),
                        credential.get("password"),
                    ),
                    addresses[0],
                )
                mediamtx.sync(camera.id, stream_url)
            except Exception as exc:
                logger.error(
                    "[%s] MediaMTX reconciliation failed: %s",
                    snapshot.id,
                    type(exc).__name__,
                )


def _handle_signal(signum, _frame) -> None:
    logger.info("종료 신호 수신(%s) — graceful shutdown 시작.", signum)
    _shutdown.set()


def _shutdown_runtime(
    pipeline: SafetyPipeline,
    alert_manager: AlertManager,
    api_server: Any = None,
    api_thread: threading.Thread | None = None,
) -> None:
    """Stop producers before flushing the durable notification outbox."""
    if api_server is not None:
        api_server.should_exit = True
    if api_thread is not None:
        api_thread.join(timeout=_API_JOIN_TIMEOUT_SECONDS)
        if api_thread.is_alive():
            logger.warning("API 서버 종료 대기 시간이 초과되었습니다.")
    pipeline.stop()
    alert_manager.shutdown()


def _start_api_server(api_server: Any) -> tuple[threading.Thread, threading.Event]:
    """Run the required API server and record an unexpected server exit."""
    api_failed = threading.Event()

    def _serve_api() -> None:
        try:
            api_server.run()  # Non-main thread preserves this process's signal handlers.
        except BaseException:
            logger.exception("API 서버 실행 실패")
            if not _shutdown.is_set():
                api_failed.set()
                _shutdown.set()
        else:
            if not _shutdown.is_set():
                logger.error("API 서버가 종료 요청 없이 중지되었습니다.")
                api_failed.set()
                _shutdown.set()

    api_thread = threading.Thread(target=_serve_api, daemon=True, name="ksi-api")
    api_thread.start()
    return api_thread, api_failed


def _wait_for_api_start(
    api_server: Any,
    api_thread: threading.Thread,
    api_failed: threading.Event,
) -> bool:
    """Wait until uvicorn has bound successfully and its serving thread is live."""
    deadline = time.monotonic() + _API_START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if api_server.started and api_thread.is_alive():
            return True
        if not api_thread.is_alive():
            if not _shutdown.is_set():
                api_failed.set()
                _shutdown.set()
            return False
        if _shutdown.wait(timeout=min(0.05, max(0.0, deadline - time.monotonic()))):
            return False

    logger.error("API 서버 시작 대기 시간이 초과되었습니다.")
    if not _shutdown.is_set():
        api_failed.set()
        _shutdown.set()
    return False


def _build_health_checker(config: AppConfig, pipeline: SafetyPipeline) -> HealthChecker:
    """Register mandatory runtime health without treating optional Ollama as a dependency."""
    checker = HealthChecker()
    checker.register("disk_space", check_disk_space)
    checker.register("memory_usage", check_memory_usage)
    # VLM health is already tracked through provider metrics by
    # register_pipeline_health_checks. A raw localhost Ollama probe would make a
    # valid detector-only appliance unhealthy before it has sent any VLM work.
    register_pipeline_health_checks(
        checker,
        pipeline,
        stale_sec=float(config.get("cameras", "stale_threshold_sec", default=30.0)),
    )
    return checker


def main() -> int:
    # main() may be invoked repeatedly by tests or an in-process supervisor.
    _shutdown.clear()
    logging.basicConfig(
        level=os.environ.get("KSI_CCTV_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ConfigLoader.load_env()
    config = AppConfig()

    logger.info("KSI-CCTV 헤드리스 탐지 데몬 시작...")
    try:
        pipeline, alert_manager, _registry = build_pipeline(config)
    except Exception as e:
        logger.error("파이프라인 조립 실패: %s", e)
        return 1
    event_repository = alert_manager.event_repository
    incident_store = alert_manager.incident_store

    camera_dir = Path(
        os.environ.get(
            "KSI_CCTV_CAMERA_DIR",
            str(Path(__file__).resolve().parents[1] / "config" / "cameras"),
        )
    )
    camera_repository = CameraRepository(camera_dir)
    camera_credentials = CredentialStore(camera_dir)
    camera_runtime_lock = threading.RLock()
    mediamtx = (
        MediaMTXClient(os.environ["KSI_CCTV_MEDIAMTX_API_URL"])
        if os.environ.get("KSI_CCTV_MEDIAMTX_API_URL") else None
    )
    rtsp_probe = RtspProbe(
        allow_loopback=os.environ.get("KSI_CCTV_ALLOW_LOOPBACK_CAMERA", "").lower()
        in {"1", "true", "yes"}
    )

    # 단일 서비스: /health* + 엔진 API(+제어)를 FastAPI/uvicorn 한 서버로 서빙(탐지+API 1프로세스).
    # 실 파이프라인 상태를 헬스에 연결(false-green 방지) + compat /health*(기존 http.server 응답 호환).
    # 카메라 등록보다 **먼저** 띄워, 카메라 0대 오설정에서도 /health로 진단 가능하게 한다.
    # 기본 loopback — 운영상태/제어 외부노출 방지. /api/*는 인증(키)으로 보호되며, 외부 모니터링/
    # 프론트 연동이 필요하면 KSI_CCTV_HEALTH_HOST=0.0.0.0로 명시 opt-in.
    health_host = os.environ.get("KSI_CCTV_HEALTH_HOST", "127.0.0.1")
    health_port = int(os.environ.get("KSI_CCTV_HEALTH_PORT", "8081"))
    api_server = None
    api_thread: threading.Thread | None = None
    api_failed: threading.Event | None = None
    exit_code = 0
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)
        try:
            import uvicorn

            from src.api.app import create_app

            api_app = create_app(
                config=config,
                pipeline=pipeline,
                checker_factory=lambda: _build_health_checker(config, pipeline),
                camera_repository=camera_repository,
                camera_credentials=camera_credentials,
                mediamtx=mediamtx,
                camera_runtime_lock=camera_runtime_lock,
                event_repository=event_repository,
                alert_manager=alert_manager,
                incident_store=incident_store,
            )
            api_server = uvicorn.Server(
                uvicorn.Config(api_app, host=health_host, port=health_port, log_level="warning")
            )
            api_thread, api_failed = _start_api_server(api_server)
        except Exception as exc:
            logger.error("API 서버 초기화 실패: %s", exc)
            exit_code = 1
            return exit_code

        if not _wait_for_api_start(api_server, api_thread, api_failed):
            exit_code = 1 if api_failed.is_set() else 0
            return exit_code

        n_cameras = _register_cameras(
            pipeline,
            config,
            camera_repository=camera_repository,
            camera_credentials=camera_credentials,
            rtsp_probe=rtsp_probe,
            mediamtx=mediamtx,
        )
        if n_cameras == 0:
            # exit 1 + Restart=on-failure는 영구 crash-loop가 되어 /health조차 못 띄운다.
            # 대신 데몬을 유지하고 /health(cameras 체크)가 degraded로 노출 → 운영자가 .env/cameras.json
            # 수정 후 재시작하면 된다(조용한 crash-loop보다 진단 가능).
            logger.error(
                "등록된 활성 카메라가 없습니다 — /health가 degraded로 표시됩니다. "
                "config/cameras.json(enabled)과 .env(CCTV_CAM_*_URL)를 확인하세요."
            )
        else:
            logger.info("카메라 %d대 등록됨.", n_cameras)

        pipeline.start()
        if api_failed.is_set() or not api_thread.is_alive():
            api_failed.set()
            _shutdown.set()
            exit_code = 1
            return exit_code
        logger.info("탐지 파이프라인 가동 중 (health: http://%s:%d/health). SIGTERM까지 실행.",
                    health_host, health_port)
        _sd_notify("READY=1")  # systemd에 기동완료 통지(비-systemd면 no-op)

        # Check the control plane at least once per second without changing the
        # configured watchdog or gateway reconciliation cadence.
        wd_usec = os.environ.get("WATCHDOG_USEC")
        watchdog_interval = (
            int(wd_usec) / 1_000_000 / 2
            if wd_usec and wd_usec.isdigit()
            else 3600.0
        )
        gateway_interval = max(
            5.0,
            float(os.environ.get("KSI_CCTV_GATEWAY_RECONCILE_SEC", "30")),
        ) if mediamtx is not None else 3600.0
        next_watchdog = time.monotonic() + watchdog_interval
        next_gateway_reconcile = time.monotonic() + gateway_interval
        gateway_reconcile_thread: threading.Thread | None = None
        while not _shutdown.is_set():
            next_check = min(next_watchdog, next_gateway_reconcile)
            _shutdown.wait(
                timeout=min(
                    _API_MONITOR_INTERVAL_SECONDS,
                    max(0.0, next_check - time.monotonic()),
                )
            )
            if _shutdown.is_set():
                break
            if api_failed.is_set() or not api_thread.is_alive():
                api_failed.set()
                _shutdown.set()
                exit_code = 1
                break

            now = time.monotonic()
            if now >= next_watchdog:
                _sd_notify("WATCHDOG=1")
                next_watchdog = now + watchdog_interval
            if (
                mediamtx is not None
                and now >= next_gateway_reconcile
                and (
                    gateway_reconcile_thread is None
                    or not gateway_reconcile_thread.is_alive()
                )
            ):
                gateway_reconcile_thread = threading.Thread(
                    target=_reconcile_managed_gateway,
                    args=(
                        camera_repository,
                        camera_credentials,
                        rtsp_probe,
                        mediamtx,
                        camera_runtime_lock,
                    ),
                    daemon=True,
                    name="mediamtx-reconcile",
                )
                gateway_reconcile_thread.start()
                next_gateway_reconcile = now + gateway_interval
        if api_failed.is_set():
            exit_code = 1
    finally:
        _sd_notify("STOPPING=1")
        logger.info("파이프라인 중지 중...")
        _shutdown_runtime(pipeline, alert_manager, api_server, api_thread)
        logger.info("종료 완료.")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
