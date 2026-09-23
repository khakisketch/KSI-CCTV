"""KSI-CCTV 엔진 API (FastAPI) — REST 엔드포인트.

읽기 엔드포인트(events/cameras/models/policies/config/health)를 제공한다. 제어(start/stop)·
실시간 스트림(SSE/WS)은 데몬 통합 단계에서 추가한다(P1 범위는 읽기 + 인증 + 마스킹).

실행: uvicorn src.api.app:app  또는  create_app() (테스트). 보호 엔드포인트는 X-API-Key 필요.
"""
from __future__ import annotations

import asyncio
import os
import threading
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse

from src.api import service
from src.api.actor import reset_actor, sanitize_actor, set_actor
from src.api.auth import require_api_key
from src.api.deps import build_health_checker, runtime_event_catalog
from src.api.routes import cameras as cameras_routes
from src.api.routes import commissioning as commissioning_routes
from src.api.routes import events as events_routes
from src.api.routes import ops as ops_routes
from src.api.routes import reports as reports_routes
from src.api.routes import system as system_routes
from src.api.routes import webhooks as webhooks_routes
from src.cameras import (
    CameraRepository,
    CredentialStore,
    MediaMTXClient,
    OnvifAdapter,
    OnvifClient,
    RtspProbe,
    WsDiscoveryClient,
)
from src.commissioning import CommissioningStore
from src.incidents import IncidentStore
from src.utils.config import AppConfig


def create_app(
    config: Any | None = None,
    pipeline: Any | None = None,
    checker_factory: Any | None = None,
    camera_directory: Path | None = None,
    camera_repository: CameraRepository | None = None,
    camera_credentials: CredentialStore | None = None,
    rtsp_probe: RtspProbe | None = None,
    onvif: OnvifAdapter | None = None,
    mediamtx: MediaMTXClient | None = None,
    commissioning_store: CommissioningStore | None = None,
    camera_runtime_lock: Any | None = None,
    event_repository: Any | None = None,
    alert_manager: Any | None = None,
    incident_store: IncidentStore | None = None,
) -> FastAPI:
    """엔진 API 앱 생성. config 미지정 시 AppConfig() 로드. pipeline 지정 시 파이프라인 헬스 포함.

    checker_factory: 헬스 HealthChecker를 만드는 호출가능(데몬이 ollama·configured stale_sec 포함한
    풍부한 체커를 주입). 미지정 시 disk/memory(+pipeline) 기본 체커.

    저장소 미주입 standalone 모드(event/alert/incident 없음)는 degraded로 명시된다
    (`app.state.standalone_degraded`). 이 모드에서는 이벤트 조회가 alerts.log
    폴백으로 동작하고 incident 저장이 없다. 운영 데몬은 반드시 저장소를 주입한다.
    """
    app = FastAPI(
        title="KSI-CCTV Engine API",
        version="1.0.0",
        description="설치형 AI CCTV 엔진 API — 이벤트·카메라·모델·정책·설정·헬스·제어",
    )
    app.state.config = config or AppConfig()
    app.state.pipeline = pipeline
    camera_dir = camera_directory or Path(
        os.environ.get(
            "KSI_CCTV_CAMERA_DIR",
            str(Path(__file__).resolve().parents[2] / "config" / "cameras"),
        )
    )
    app.state.camera_repository = camera_repository or CameraRepository(camera_dir)
    app.state.camera_credentials = camera_credentials or CredentialStore(camera_dir)
    app.state.commissioning = commissioning_store or CommissioningStore(camera_dir)
    app.state.rtsp_probe = rtsp_probe or RtspProbe(
        allow_loopback=os.environ.get("KSI_CCTV_ALLOW_LOOPBACK_CAMERA", "").lower()
        in {"1", "true", "yes"}
    )
    discovery = WsDiscoveryClient()
    app.state.onvif = onvif or OnvifAdapter(
        client_factory=OnvifClient(discovery),
        discovery=discovery,
    )
    app.state.mediamtx = mediamtx or (
        MediaMTXClient(os.environ["KSI_CCTV_MEDIAMTX_API_URL"])
        if os.environ.get("KSI_CCTV_MEDIAMTX_API_URL") else None
    )
    app.state.camera_runtime_lock = camera_runtime_lock or threading.RLock()
    app.state.event_repository = event_repository
    app.state.durable_event_store_status = (
        service.DurableEventStoreStatus() if event_repository is not None else None
    )
    app.state.alert_manager = alert_manager
    if alert_manager is not None:
        alert_manager.durable_store_status = app.state.durable_event_store_status
    app.state.incident_store = incident_store
    app.state.model_upload_lock = asyncio.Lock()
    app.state.standalone_degraded = (
        event_repository is None and alert_manager is None and incident_store is None
    )

    auth = [Depends(require_api_key)]

    @app.middleware("http")
    async def capture_actor(request: Request, call_next):
        """Capture the console `X-KSI-Actor` header into request context.

        `write_audit` records this value without touching its 32 call sites.
        Exception-safe: any header/read failure falls back to "system".
        """
        try:
            raw_actor = request.headers.get("x-ksi-actor")
        except Exception:
            raw_actor = None
        token = set_actor(sanitize_actor(raw_actor))
        try:
            return await call_next(request)
        finally:
            reset_actor(token)

    @app.middleware("http")
    async def guard_model_upload_body(request: Request, call_next):
        """Authenticate, bound, and serialize model multipart parsing before Starlette spools it."""
        if request.method != "POST" or request.url.path != "/api/models/upload":
            return await call_next(request)

        try:
            require_api_key(request.headers.get("x-api-key"))
        except HTTPException as error:
            return JSONResponse(
                {"detail": error.detail},
                status_code=error.status_code,
                headers=error.headers,
            )

        from src.models import upload_validator

        raw_length = request.headers.get("content-length")
        try:
            content_length = int(raw_length or "")
        except ValueError:
            return JSONResponse({"detail": "모델 업로드에는 Content-Length가 필요합니다."}, status_code=411)
        max_request_bytes = upload_validator.MAX_FILE_SIZE_BYTES + 1024 * 1024
        if content_length <= 0 or content_length > max_request_bytes:
            return JSONResponse(
                {"detail": f"업로드 요청이 너무 큽니다(파일 최대 {upload_validator.MAX_FILE_SIZE_BYTES // (1024 * 1024)}MB)."},
                status_code=413,
            )

        upload_lock = app.state.model_upload_lock
        if upload_lock.locked():
            return JSONResponse({"detail": "다른 모델 업로드를 검증 중입니다. 잠시 후 다시 시도하세요."}, status_code=429)
        await upload_lock.acquire()
        try:
            return await call_next(request)
        finally:
            upload_lock.release()

    @app.middleware("http")
    async def audit_failed_mutations(request: Request, call_next):
        """Record failed protected mutations without logging request bodies/secrets.

        Successful mutations keep their existing semantic audit events (camera_add,
        pipeline_start, model_update, ...). This middleware fills the historical gap
        where validation/auth/runtime failures disappeared from the audit trail.
        """
        is_mutation = request.method in {"POST", "PUT", "PATCH", "DELETE"}
        is_engine_api = request.url.path.startswith("/api/")
        try:
            response = await call_next(request)
        except Exception:
            if is_mutation and is_engine_api:
                service.write_audit(
                    "api_mutation_failed",
                    actor_type="api-key" if request.headers.get("x-api-key") else "unknown",
                    actor="api-key" if request.headers.get("x-api-key") else "unknown",
                    outcome="failure",
                    status_code=500,
                    method=request.method,
                    request_path=request.url.path,
                    failure_reason="unhandled_exception",
                )
            raise
        if is_mutation and is_engine_api and response.status_code >= 400:
            service.write_audit(
                "api_mutation_failed",
                actor_type="api-key" if request.headers.get("x-api-key") else "unknown",
                actor="api-key" if request.headers.get("x-api-key") else "unknown",
                outcome="failure",
                status_code=response.status_code,
                method=request.method,
                request_path=request.url.path,
                failure_reason="request_rejected",
            )
        return response

    _build_checker = partial(build_health_checker, app, checker_factory)
    _runtime_event_catalog = partial(runtime_event_catalog, app)

    # ── 헬스/제어(routes/ops.py로 이동 — 등록 순서 불변) ─────────────────────
    ops_routes.register(app, auth, build_checker=_build_checker)

    # ── 이벤트(routes/events.py로 이동 — 등록 순서 불변) ─────────────────────
    events_routes.register(app, auth)

    # ── 카메라(routes/cameras.py로 이동 — 등록 순서 불변) ─────────────────────
    cameras_routes.register(app, auth)

    # ── 시스템/플랫폼 그룹 (routes/system.py로 분리, U12a) ────────────────
    system_routes.register(
        app, auth, build_checker=_build_checker, runtime_event_catalog=_runtime_event_catalog
    )

    # ── 커미셔닝(routes/commissioning.py로 이동 — 등록 순서 불변) ─────────
    commissioning_routes.register(
        app, auth, build_checker=_build_checker, runtime_event_catalog=_runtime_event_catalog
    )

    # ── 웹훅 엔드포인트 관리(P2-T3 — 신규 경로만 추가, 기존 불변) ──────────
    webhooks_routes.register(app, auth)

    # ── 정기 보고서 조회(P3-T4 — 읽기 전용 3종, 기존 불변) ─────────────────
    reports_routes.register(app, auth)

    return app


# 모듈 직접 실행/임포트용 standalone 앱. 저장소 미주입 degraded 모드이며
# 운영 데몬(run.py)은 저장소를 주입한 create_app()을 사용한다.
app = create_app()
