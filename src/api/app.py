"""KSI-CCTV 엔진 API (FastAPI) — REST 엔드포인트.

읽기 엔드포인트(events/cameras/models/policies/config/health)를 제공한다. 제어(start/stop)·
실시간 스트림(SSE/WS)은 데몬 통합 단계에서 추가한다(P1 범위는 읽기 + 인증 + 마스킹).

실행: uvicorn src.api.app:app  또는  create_app() (테스트). 보호 엔드포인트는 X-API-Key 필요.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import zipfile
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
)
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from src.api import service
from src.api.auth import require_api_key
from src.api.routes import cameras as cameras_routes
from src.api.routes import events as events_routes
from src.api.routes import system as system_routes
from src.cameras import (
    CameraRepository,
    CredentialStore,
    MediaMTXClient,
    OnvifAdapter,
    OnvifClient,
    RtspProbe,
    WsDiscoveryClient,
)
from src.commissioning import CommissioningConflict, CommissioningStore
from src.incidents import IncidentStore
from src.utils.config import AppConfig
from src.utils.health import (
    HealthChecker,
    check_disk_space,
    check_memory_usage,
    register_pipeline_health_checks,
)


class CommissioningSiteBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str = Field(default="", max_length=256)
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    installer: str = Field(default="", max_length=128)
    expected_camera_count: int = Field(default=0, ge=0, le=16)
    expected_revision: int | None = Field(default=None, ge=0)


class CameraCommissioningBody(BaseModel):
    live_verified: bool = False
    reconnect_verified: bool = False
    detection_verified: bool = False
    alert_verified: bool = False
    notes: str = Field(default="", max_length=1000)
    expected_revision: int | None = Field(default=None, ge=0)


class CommissioningRevisionBody(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)


class HandoffAcceptanceBody(BaseModel):
    customer_name: str = Field(min_length=1, max_length=128)
    notes: str = Field(default="", max_length=1000)
    expected_revision: int | None = Field(default=None, ge=0)


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

    def _build_checker() -> HealthChecker:
        if checker_factory is not None:
            checker = checker_factory()
        else:
            checker = HealthChecker()
            checker.register("disk_space", check_disk_space)
            checker.register("memory_usage", check_memory_usage)
        if app.state.durable_event_store_status is not None:
            checker.register(
                "durable_event_store", app.state.durable_event_store_status.health_check
            )
        if app.state.pipeline is not None:
            register_pipeline_health_checks(checker, app.state.pipeline)
        return checker

    def _runtime_event_catalog() -> dict[str, Any]:
        catalog = service.get_event_catalog()
        pipeline_obj = app.state.pipeline
        registry = getattr(pipeline_obj, "model_registry", None)
        loaded_models = set(getattr(registry, "active_model_names", []) or [])
        legacy_detector = getattr(pipeline_obj, "detector", None)
        ai_available = bool(legacy_detector is not None or loaded_models)
        for item in catalog.get("catalog", []):
            if not isinstance(item, dict):
                continue
            runtime = item.get("runtime")
            item["runtime_available"] = runtime == "health" or ai_available
            if item.get("selectable") and runtime in {"detection", "tracking"} and not ai_available:
                item["selectable"] = False
                item["blocked_reason"] = "활성화된 AI 모델이 없어 현재 장비에서 실행할 수 없습니다."
                item["reason"] = item["blocked_reason"]
        catalog["runtime"] = {
            "ai_available": ai_available,
            "loaded_models": sorted(loaded_models),
            "ai_runtime": (
                pipeline_obj.get_ai_runtime_status()
                if pipeline_obj is not None and hasattr(pipeline_obj, "get_ai_runtime_status")
                else {}
            ),
        }
        return catalog

    def _commissioning_snapshot() -> dict[str, Any]:
        state = app.state.commissioning.read()
        _, managed = app.state.camera_repository.list()
        pipeline_obj = app.state.pipeline
        try:
            pipeline_status = (
                pipeline_obj.get_status()
                if pipeline_obj is not None and hasattr(pipeline_obj, "get_status")
                else {}
            ) or {}
        except Exception:
            pipeline_status = {}
        telemetry = pipeline_status.get("cameras", {})
        if not isinstance(telemetry, dict):
            telemetry = {}
        health = _build_checker().run_checks().to_dict()
        event_catalog = _runtime_event_catalog()
        catalog_by_name = {
            item["name"]: item
            for item in event_catalog.get("catalog", [])
            if isinstance(item, dict) and item.get("name")
        }

        checks: list[dict[str, Any]] = []

        def add_check(
            check_id: str,
            label: str,
            status: str,
            summary: str,
            remediation: str,
        ) -> None:
            checks.append({
                "id": check_id,
                "label": label,
                "status": status,
                "summary": summary,
                "remediation": remediation,
            })

        site = state["site"]
        site_ready = bool(str(site.get("name", "")).strip())
        add_check(
            "site",
            "현장 정보",
            "pass" if site_ready else "fail",
            f"현장: {site['name']}" if site_ready else "현장 이름이 아직 설정되지 않았습니다.",
            "설치 진행에서 현장 이름과 예상 카메라 대수를 저장하세요.",
        )
        add_check(
            "engine",
            "관리 서비스",
            "pass",
            "관리 API가 정상 응답 중입니다.",
            "문제가 반복되면 진단 화면에서 서비스 상태를 다시 확인하세요.",
        )
        gateway_ready = app.state.mediamtx is not None
        add_check(
            "gateway",
            "영상 서비스",
            "pass" if gateway_ready else "fail",
            "영상 Gateway가 구성되어 있습니다." if gateway_ready else "영상 Gateway가 구성되지 않았습니다.",
            "Appliance 서비스 상태와 KSI_CCTV_MEDIAMTX_API_URL 설정을 확인하세요.",
        )
        pipeline_running = bool(pipeline_status.get("running"))
        add_check(
            "pipeline",
            "분석 서비스",
            "pass" if pipeline_running else "fail",
            "분석 Pipeline이 실행 중입니다." if pipeline_running else "분석 Pipeline이 중지되었거나 연결되지 않았습니다.",
            "대시보드에서 분석 서비스를 시작한 뒤 다시 검사하세요.",
        )
        system_ready = health.get("status") == "healthy"
        add_check(
            "system",
            "장비 자원",
            "pass" if system_ready else "fail",
            "메모리와 저장공간이 정상 범위입니다." if system_ready else "하나 이상의 장비 상태 검사가 실패했습니다.",
            "진단 화면에서 실패한 항목의 사용률과 서비스 메시지를 확인하세요.",
        )

        expected = int(site.get("expected_camera_count") or 0)
        camera_count_ok = bool(managed) and (expected == 0 or len(managed) >= expected)
        if camera_count_ok:
            camera_summary = f"관리형 카메라 {len(managed)}대가 등록되었습니다."
        elif expected > len(managed):
            camera_summary = f"예상 {expected}대 중 {len(managed)}대만 등록되었습니다."
        else:
            camera_summary = "등록된 관리형 카메라가 없습니다."
        add_check(
            "inventory",
            "카메라 목록",
            "pass" if camera_count_ok else "fail",
            camera_summary,
            "카메라 메뉴에서 누락된 카메라를 검색하거나 직접 등록하세요.",
        )

        camera_rows: list[dict[str, Any]] = []
        zone_issues: list[str] = []
        runtime_issues: list[str] = []
        acceptance_issues: list[str] = []
        for camera in managed:
            status = telemetry.get(camera.id, {})
            if not isinstance(status, dict):
                status = {}
            receiving = status.get("health_state") == "receiving"
            zone_warnings = service.event_zone_warnings(
                list(camera.enabled_events), list(camera.zones)
            )
            if zone_warnings:
                zone_issues.append(camera.name)
            ai_events = [
                name
                for name in camera.enabled_events
                if catalog_by_name.get(name, {}).get("runtime") in {"detection", "tracking"}
            ]
            ai_available = bool(event_catalog.get("runtime", {}).get("ai_available"))
            if ai_events and not ai_available:
                runtime_issues.append(camera.name)
            saved_checks = state["camera_checks"].get(camera.id, {})
            acceptance_ready = bool(
                saved_checks.get("live_verified")
                and saved_checks.get("reconnect_verified")
                and (not ai_events or saved_checks.get("detection_verified"))
                and (not camera.enabled_events or saved_checks.get("alert_verified"))
            )
            if not acceptance_ready:
                acceptance_issues.append(camera.name)
            camera_ready = bool(
                camera.enabled
                and receiving
                and not zone_warnings
                and not (ai_events and not ai_available)
                and acceptance_ready
            )
            camera_rows.append({
                "id": camera.id,
                "name": camera.name,
                "location": camera.location,
                "enabled": camera.enabled,
                "health_state": status.get("health_state", "unavailable"),
                "fps": status.get("fps"),
                "dropped_frames": status.get("dropped_frames"),
                "seconds_since_last_frame": status.get("seconds_since_last_frame"),
                "reconnect_attempts": status.get("reconnect_attempts"),
                "consecutive_failures": status.get("consecutive_failures"),
                "enabled_events": list(camera.enabled_events),
                "zone_count": len(camera.zones),
                "zone_warnings": zone_warnings,
                "ai_events": ai_events,
                "checks": saved_checks,
                "ready": camera_ready,
            })

        add_check(
            "camera_runtime",
            "카메라 영상 수신",
            "pass" if managed and all(row["health_state"] == "receiving" for row in camera_rows) else "fail",
            "모든 카메라가 정상 수신 중입니다." if managed and all(row["health_state"] == "receiving" for row in camera_rows) else "수신 상태를 확인해야 하는 카메라가 있습니다.",
            "카메라별 연결 검사와 최근 프레임 시간을 확인하세요.",
        )
        add_check(
            "zones",
            "필수 영역",
            "pass" if not zone_issues else "fail",
            "선택한 감지 항목에 필요한 영역이 준비되었습니다." if not zone_issues else f"영역 설정 필요: {', '.join(zone_issues)}",
            "카메라 상세에서 제한구역, 배회구역 또는 통과선을 먼저 설정하세요.",
        )
        add_check(
            "ai_runtime",
            "AI 실행 준비",
            "pass" if not runtime_issues else "fail",
            "선택한 감지 항목을 실행할 수 있습니다." if not runtime_issues else f"활성 AI 모델 없음: {', '.join(runtime_issues)}",
            "관리자 모델 화면에서 필요한 모델을 설치·활성화하고 추론 상태를 확인하세요.",
        )
        add_check(
            "acceptance",
            "카메라 인수시험",
            "pass" if managed and not acceptance_issues else "fail",
            "모든 카메라 인수시험이 기록되었습니다." if managed and not acceptance_issues else f"시험 미완료: {', '.join(acceptance_issues) if acceptance_issues else '등록 카메라 없음'}",
            "설치 진행에서 라이브, 재연결, 탐지, 알림 시험 결과를 기록하세요.",
        )

        blocking = [check for check in checks if check["status"] == "fail"]
        passed = sum(check["status"] == "pass" for check in checks)
        return {
            "schema_version": 1,
            "revision": state["revision"],
            "site": site,
            "completed_at": state.get("completed_at"),
            "handoff": state.get("handoff", {}),
            "updated_at": state.get("updated_at"),
            "ready_to_complete": len(blocking) == 0,
            "progress": {
                "passed": passed,
                "total": len(checks),
                "percent": round((passed / len(checks)) * 100) if checks else 0,
            },
            "checks": checks,
            "cameras": camera_rows,
            "health": health,
            "event_runtime": event_catalog.get("runtime", {}),
        }

    # ── 헬스(/api 인증 뷰) ───────────────────────────────────────────────
    @app.get("/api/health/live", tags=["health"])
    def health_live() -> dict[str, str]:
        """liveness 프로브 — 프로세스 생존 시 항상 200(비인증)."""
        return {"status": "alive"}

    @app.get("/api/health", tags=["health"], dependencies=auth)
    def health() -> dict[str, Any]:
        return _build_checker().run_checks().to_dict()

    # ── 헬스(compat: 기존 http.server /health* 대체 — 비인증, 컨테이너/systemd 프로브용) ──
    @app.get("/health/live", tags=["health"], include_in_schema=False)
    @app.get("/healthz", tags=["health"], include_in_schema=False)
    def compat_live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health", tags=["health"], include_in_schema=False)
    def compat_health() -> JSONResponse:
        report = _build_checker().run_checks()
        # 비인증 compatibility probe는 liveness/readiness 용도만 제공한다. component
        # messages, disk/GPU metrics, camera/model metadata는 인증된 /api/health에서만 조회.
        return JSONResponse(
            {"status": "healthy" if report.overall_healthy else "unhealthy"},
            status_code=200 if report.overall_healthy else 503,
        )

    @app.get("/health/ready", tags=["health"], include_in_schema=False)
    def compat_ready() -> JSONResponse:
        report = _build_checker().run_checks()
        components = {component.name: component for component in report.components}
        required = {"disk_space", "memory_usage"}
        if app.state.pipeline is not None:
            required.update({"cameras", "model_registry"})
        ok = all(
            name in components and components[name].healthy
            for name in required
        )
        camera_health = components.get("cameras")
        if app.state.pipeline is not None:
            ok = ok and bool(
                camera_health
                and camera_health.metadata.get("camera_count", 0) > 0
            )
        return JSONResponse(
            {
                "status": "ready" if ok else "not_ready",
                "required": sorted(required),
            },
            status_code=200 if ok else 503,
        )

    # ── 제어(라이브 파이프라인 — 데몬에 mount된 경우만) ──────────────────
    @app.get("/api/control/status", tags=["control"], dependencies=auth)
    def control_status() -> dict[str, Any]:
        pl = app.state.pipeline
        if pl is None:
            return {"connected": False, "running": False}
        status = pl.get_status() if hasattr(pl, "get_status") else {}
        return {"connected": True, "running": bool(getattr(pl, "is_running", False)), "status": status}

    @app.post("/api/control/start", tags=["control"], dependencies=auth)
    def control_start() -> dict[str, Any]:
        pl = app.state.pipeline
        if pl is None:
            raise HTTPException(status_code=503, detail="파이프라인이 연결되지 않았습니다(데몬 외 실행).")
        if not getattr(pl, "is_running", False):
            pl.start()
        service.write_audit("pipeline_start")
        return {"running": bool(getattr(pl, "is_running", True))}

    @app.post("/api/control/stop", tags=["control"], dependencies=auth)
    def control_stop() -> dict[str, Any]:
        pl = app.state.pipeline
        if pl is None:
            raise HTTPException(status_code=503, detail="파이프라인이 연결되지 않았습니다(데몬 외 실행).")
        if getattr(pl, "is_running", False):
            pl.stop()
        service.write_audit("pipeline_stop")
        return {"running": bool(getattr(pl, "is_running", False))}

    # ── 이벤트(routes/events.py로 이동 — 등록 순서 불변) ─────────────────────
    events_routes.register(app, auth)

    # ── 카메라(routes/cameras.py로 이동 — 등록 순서 불변) ─────────────────────
    cameras_routes.register(app, auth)

    # ── 시스템/플랫폼 그룹 (routes/system.py로 분리, U12a) ────────────────
    system_routes.register(
        app, auth, build_checker=_build_checker, runtime_event_catalog=_runtime_event_catalog
    )

    # ── 설치 진행·인수시험 ───────────────────────────────────────────────
    @app.get("/api/video-sources/capabilities", tags=["commissioning"], dependencies=auth)
    def video_source_capabilities() -> dict[str, Any]:
        return {
            "adapters": [
                {
                    "kind": "onvif",
                    "label": "ONVIF 네트워크 카메라",
                    "available": True,
                    "discovery": True,
                    "description": "같은 CCTV 네트워크에서 장비와 영상 프로필을 검색합니다.",
                },
                {
                    "kind": "rtsp",
                    "label": "RTSP 카메라",
                    "available": True,
                    "discovery": False,
                    "description": "카메라 또는 NVR 채널의 RTSP 주소를 직접 등록합니다.",
                },
                {
                    "kind": "gateway",
                    "label": "연결된 영상 Gateway",
                    "available": app.state.mediamtx is not None,
                    "discovery": app.state.mediamtx is not None,
                    "description": "Appliance 영상 Gateway에 이미 게시된 스트림을 사용합니다.",
                },
                {
                    "kind": "v4l2",
                    "label": "로컬 USB 카메라",
                    "available": False,
                    "discovery": False,
                    "description": "현재 버전은 관리형 RTSP bridge를 통해 등록합니다.",
                    "reason": "USB 장치 lifecycle adapter는 후속 하드웨어 검증이 필요합니다.",
                },
            ]
        }

    @app.get("/api/preflight", tags=["commissioning"], dependencies=auth)
    def preflight() -> dict[str, Any]:
        snapshot = _commissioning_snapshot()
        return {
            "ready": snapshot["ready_to_complete"],
            "progress": snapshot["progress"],
            "checks": snapshot["checks"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/commissioning", tags=["commissioning"], dependencies=auth)
    def commissioning_status() -> dict[str, Any]:
        return _commissioning_snapshot()

    @app.put("/api/commissioning/site", tags=["commissioning"], dependencies=auth)
    def commissioning_site(body: CommissioningSiteBody) -> dict[str, Any]:
        try:
            app.state.commissioning.update_site(
                body.model_dump(exclude={"expected_revision"}),
                body.expected_revision,
            )
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_site_updated", expected_camera_count=body.expected_camera_count)
        return _commissioning_snapshot()

    @app.put(
        "/api/commissioning/cameras/{camera_id}",
        tags=["commissioning"],
        dependencies=auth,
    )
    def commissioning_camera(camera_id: str, body: CameraCommissioningBody) -> dict[str, Any]:
        if app.state.camera_repository.get(camera_id) is None:
            raise HTTPException(status_code=404, detail="camera not found")
        try:
            app.state.commissioning.update_camera(
                camera_id,
                body.model_dump(exclude={"expected_revision"}),
                body.expected_revision,
            )
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("camera_commissioning_updated", camera=camera_id)
        return _commissioning_snapshot()

    @app.post("/api/commissioning/complete", tags=["commissioning"], dependencies=auth)
    def commissioning_complete(body: CommissioningRevisionBody) -> dict[str, Any]:
        snapshot = _commissioning_snapshot()
        if not snapshot["ready_to_complete"]:
            failed = [check["label"] for check in snapshot["checks"] if check["status"] == "fail"]
            raise HTTPException(
                status_code=409,
                detail="완료되지 않은 필수 검사: " + ", ".join(failed),
            )
        try:
            app.state.commissioning.mark_completed(body.expected_revision)
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_completed")
        return _commissioning_snapshot()

    @app.post("/api/commissioning/reopen", tags=["commissioning"], dependencies=auth)
    def commissioning_reopen(body: CommissioningRevisionBody) -> dict[str, Any]:
        try:
            app.state.commissioning.reopen(body.expected_revision)
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_reopened")
        return _commissioning_snapshot()

    @app.put("/api/commissioning/handoff", tags=["commissioning"], dependencies=auth)
    def commissioning_handoff(body: HandoffAcceptanceBody) -> dict[str, Any]:
        try:
            app.state.commissioning.record_handoff(
                body.customer_name,
                body.notes,
                body.expected_revision,
            )
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_handoff_accepted")
        return _commissioning_snapshot()

    @app.get("/api/commissioning/report", tags=["commissioning"], dependencies=auth)
    def commissioning_report(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        snapshot = _commissioning_snapshot()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "product": "KSI-CCTV",
            "site": snapshot["site"],
            "revision": snapshot["revision"],
            "completed_at": snapshot["completed_at"],
            "handoff": snapshot["handoff"],
            "ready": snapshot["ready_to_complete"],
            "progress": snapshot["progress"],
            "checks": snapshot["checks"],
            "cameras": snapshot["cameras"],
        }

    @app.get("/api/support/bundle", tags=["commissioning"], dependencies=auth)
    def support_bundle() -> Response:
        """Download a redacted diagnostic bundle without credentials or source URLs."""
        snapshot = _commissioning_snapshot()
        generated_at = datetime.now(timezone.utc).isoformat()
        health = snapshot["health"]
        system = health.get("system") if isinstance(health, dict) else {}
        safe_system = {
            name: system[name]
            for name in (
                "cpu_percent",
                "memory_percent",
                "memory_used_mb",
                "memory_total_mb",
                "disk_percent",
                "disk_used_gb",
                "disk_total_gb",
                "gpu_percent",
                "gpu_memory_percent",
                "gpu_temperature",
            )
            if isinstance(system, dict) and isinstance(system.get(name), (int, float))
        }
        components = health.get("components") if isinstance(health, dict) else []
        safe_health = {
            "status": health.get("status") if isinstance(health, dict) and health.get("status") in {"healthy", "unhealthy"} else "unknown",
            "system": safe_system,
            "component_count": len(components) if isinstance(components, list) else 0,
            "unhealthy_component_count": sum(
                1 for component in components
                if isinstance(component, dict) and component.get("healthy") is False
            ) if isinstance(components, list) else 0,
            "ai_runtime": snapshot["event_runtime"].get("ai_runtime", {}),
        }
        commissioning = {
            # Site identity and location belong to the commissioning report, not a portable support archive.
            "site_configured": bool(snapshot["site"]),
            "completed_at": snapshot["completed_at"],
            "progress": snapshot["progress"],
            # Check summaries and camera names/locations can contain site-specific information.
            "checks": [
                {"id": check["id"], "status": check["status"]}
                for check in snapshot["checks"]
            ],
            "cameras": [
                {
                    "enabled": camera["enabled"],
                    "health_state": camera["health_state"],
                    "fps": camera["fps"],
                    "dropped_frames": camera["dropped_frames"],
                    "seconds_since_last_frame": camera["seconds_since_last_frame"],
                    "reconnect_attempts": camera["reconnect_attempts"],
                    "consecutive_failures": camera["consecutive_failures"],
                    "enabled_events": camera["enabled_events"],
                    "zone_count": camera["zone_count"],
                    "ready": camera["ready"],
                }
                for camera in snapshot["cameras"]
            ],
            "event_runtime": snapshot["event_runtime"],
        }
        audit_entries, audit_anchor = service.list_audit_window(limit=200)
        files = {
            "commissioning.json": commissioning,
            "health.json": safe_health,
            # Bounded evidence remains independently verifiable after audit log retention advances.
            "audit.json": {"entries": audit_entries, "anchor_previous_hash": audit_anchor},
        }
        encoded_files = {
            name: json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            for name, data in files.items()
        }
        manifest = {
            "schema_version": 2,
            "product": "KSI-CCTV",
            "generated_at": generated_at,
            "files": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in encoded_files.items()
            },
            "audit_public_key": service.audit_public_key(),
            "redacted": True,
        }
        manifest["signature"] = service.sign_audit_data(service.canonical_json_bytes(manifest))
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, data in encoded_files.items():
                archive.writestr(name, data)
        service.write_audit("support_bundle_created")
        return Response(
            content=payload.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=ksi-cctv-support.zip",
                "Cache-Control": "no-store",
            },
        )

    return app


# 모듈 직접 실행/임포트용 standalone 앱. 저장소 미주입 degraded 모드이며
# 운영 데몬(run.py)은 저장소를 주입한 create_app()을 사용한다.
app = create_app()
