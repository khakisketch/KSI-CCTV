"""KSI-CCTV 헤드리스 무인 탐지 러너 — systemd/Docker ExecStart 진입점.

대시보드 "▶️ 시작" 버튼 없이 파이프라인을 자동 기동하는 24시간 무인 운영용 데몬이다.
구성: 설정 로드 → MultiModelRegistry + EventPolicyEngine + SafetyPipeline + AlertManager
조립 → 카메라 등록 → /health 엔드포인트에 **실 파이프라인 상태**(카메라/VLM/모델 health) 연결
→ pipeline.start() → SIGTERM/SIGINT까지 블록.

대시보드(scripts/run_dashboard.sh)는 별도의 운영자 도구로, 이 데몬과 독립 실행된다.
이 데몬이 탐지·알람(이메일/로그/캡처)·헬스를 책임진다.
"""
from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any

sys.path.append(str(Path(__file__).parent.parent))

from src.alerts.alert_manager import AlertConfig, AlertManager
from src.detection.model_registry import MultiModelRegistry
from src.pipeline.pipeline import SafetyEvent, SafetyPipeline
from src.policy.event_policy_engine import EventPolicyEngine
from src.utils.config import AppConfig, ConfigLoader
from src.utils.health import (
    HealthChecker,
    check_disk_space,
    check_memory_usage,
    check_ollama_available,
    register_pipeline_health_checks,
)
from src.vlm.vlm_inference import create_vlm_provider

logger = logging.getLogger("ksi_cctv.run")

# 종료 신호 수신 시 set되어 메인 루프를 깨운다.
_shutdown = threading.Event()


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
    registry = MultiModelRegistry.from_config("./config/model_profiles.yaml")
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
    target_classes = det_cfg.get("target_classes", "person,fire,smoke,fall")
    if isinstance(target_classes, str):
        target_classes = [s.strip() for s in target_classes.split(",") if s.strip()]
    vlm_trigger = det_cfg.get("vlm_trigger_classes", "person")
    if isinstance(vlm_trigger, str):
        vlm_trigger = [s.strip() for s in vlm_trigger.split(",") if s.strip()]

    pipeline = SafetyPipeline(
        detector=primary_detector,
        skip_frames=int(det_cfg.get("skip_frames", 5)),
        alert_cooldown=float(det_cfg.get("alert_cooldown_sec", 30.0)),
        target_classes=target_classes,
        vlm_trigger_classes=vlm_trigger,
        vlm_cooldown=float(det_cfg.get("vlm_cooldown_sec", 60.0)),
        model_registry=registry,
        policy_engine=engine,
    )

    alerts_cfg = config.get_alert_config()
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
        encrypt_images=bool(alerts_cfg.get("encrypt_images", False)),
    ))

    def on_alert(event: SafetyEvent) -> None:
        alert_manager.handle_event(event)

    pipeline.add_alert_callback(on_alert)

    # VLM 활성화 (설정 시) — provider 팩토리로 config의 provider 키를 따라 생성(선택적 컴포넌트).
    vlm_cfg = config.get("models", "vlm", default={}) or {}
    provider = create_vlm_provider(vlm_cfg)
    if provider is not None:
        pipeline.enable_vlm(
            provider, vlm_cfg.get("prompt_template", "./config/prompts/construction.yaml")
        )
        logger.info(
            "VLM 활성화: provider=%s model=%s",
            vlm_cfg.get("provider", "ollama"),
            vlm_cfg.get("model_name", "qwen3-vl:latest"),
        )
    else:
        logger.info("VLM 비활성(2차 정밀분석 없음) — 1차 YOLO 탐지만 수행.")

    return pipeline, alert_manager, registry


def _register_cameras(pipeline: SafetyPipeline, config: AppConfig) -> int:
    """config의 활성 카메라를 파이프라인에 등록. 등록된 카메라 수 반환."""
    cameras = [c for c in config.load_cameras() if c.get("enabled")]
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
        pipeline.set_camera_profile(cam_id, _camera_policy_profile(cam, profiles))
        pipeline.add_camera(
            cam_id, stream_url,
            target_fps=int(cam.get("fps", 30)),
            stale_threshold=stale_threshold,
        )
        registered += 1
    return registered


def _handle_signal(signum, _frame) -> None:
    logger.info("종료 신호 수신(%s) — graceful shutdown 시작.", signum)
    _shutdown.set()


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("KSI_CCTV_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    ConfigLoader.load_env()
    config = AppConfig()

    logger.info("KSI-CCTV 헤드리스 탐지 데몬 시작...")
    try:
        pipeline, _alert_manager, _registry = build_pipeline(config)
    except Exception as e:
        logger.error("파이프라인 조립 실패: %s", e)
        return 1

    # 단일 서비스: /health* + 엔진 API(+제어)를 FastAPI/uvicorn 한 서버로 서빙(탐지+API 1프로세스).
    # 실 파이프라인 상태를 헬스에 연결(false-green 방지) + compat /health*(기존 http.server 응답 호환).
    # 카메라 등록보다 **먼저** 띄워, 카메라 0대 오설정에서도 /health로 진단 가능하게 한다.
    def _checker_factory() -> HealthChecker:
        checker = HealthChecker()
        checker.register("disk_space", check_disk_space)
        checker.register("memory_usage", check_memory_usage)
        checker.register("ollama", check_ollama_available)
        # 카메라 stale 임계를 config 값으로 통일(add_camera와 동일 SSOT).
        register_pipeline_health_checks(
            checker, pipeline,
            stale_sec=float(config.get("cameras", "stale_threshold_sec", default=30.0)),
        )
        return checker

    # 기본 loopback — 운영상태/제어 외부노출 방지. /api/*는 인증(키)으로 보호되며, 외부 모니터링/
    # 프론트 연동이 필요하면 KSI_CCTV_HEALTH_HOST=0.0.0.0로 명시 opt-in.
    health_host = os.environ.get("KSI_CCTV_HEALTH_HOST", "127.0.0.1")
    health_port = int(os.environ.get("KSI_CCTV_HEALTH_PORT", "8081"))
    api_server = None
    try:
        import uvicorn

        from src.api.app import create_app
        api_app = create_app(config=config, pipeline=pipeline, checker_factory=_checker_factory)
        api_server = uvicorn.Server(
            uvicorn.Config(api_app, host=health_host, port=health_port, log_level="warning")
        )

        def _serve_api() -> None:
            try:
                api_server.run()  # 비-메인 스레드라 uvicorn 시그널 핸들러는 자동 skip(데몬 SIGTERM 보존)
            except Exception as exc:
                logger.error("API 서버 기동 실패(탐지는 계속): %s", exc)

        threading.Thread(target=_serve_api, daemon=True, name="ksi-api").start()
    except Exception as e:
        logger.warning("API 서버 초기화 실패(탐지는 계속): %s", e)

    n_cameras = _register_cameras(pipeline, config)
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

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    pipeline.start()
    logger.info("탐지 파이프라인 가동 중 (health: http://%s:%d/health). SIGTERM까지 실행.",
                health_host, health_port)
    _sd_notify("READY=1")  # systemd에 기동완료 통지(비-systemd면 no-op)

    # 종료 신호까지 블록(데몬). _shutdown.wait()는 SIGTERM 핸들러가 set하면 깨어난다.
    # systemd WatchdogSec가 설정되면 WATCHDOG_USEC의 절반 주기로 깨어나 WATCHDOG=1을 ping한다
    # (hang 시 ping 중단 → systemd가 재시작). 비-systemd면 1시간마다 깨어나며 ping은 no-op.
    wd_usec = os.environ.get("WATCHDOG_USEC")
    wait_interval = (int(wd_usec) / 1_000_000 / 2) if (wd_usec and wd_usec.isdigit()) else 3600.0
    try:
        while not _shutdown.wait(timeout=wait_interval):
            _sd_notify("WATCHDOG=1")
    finally:
        _sd_notify("STOPPING=1")
        if api_server is not None:
            api_server.should_exit = True  # uvicorn graceful stop
        logger.info("파이프라인 중지 중...")
        pipeline.stop()
        logger.info("종료 완료.")
    return 0


if __name__ == "__main__":
    # graceful stop 후 onnxruntime 등 네이티브 라이브러리의 인터프리터-종료 시 destructor가
    # 'terminate called without an active exception'으로 비정상 종료코드(134)를 내는 것을 피하기
    # 위해, 정리를 마친 뒤 os._exit로 깨끗한 종료코드를 확정한다(systemd 오인 방지).
    _code = main()
    logging.shutdown()
    os._exit(_code)
