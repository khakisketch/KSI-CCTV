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
import secrets
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from functools import wraps
from io import BytesIO
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, model_validator

from src.api import service
from src.api.auth import check_api_key, require_api_key
from src.cameras import (
    CameraAlreadyExists,
    CameraConfig,
    CameraInput,
    CameraLimitReached,
    CameraRepository,
    CameraZone,
    CredentialStore,
    MediaMTXClient,
    MediaMTXError,
    OnvifAdapter,
    OnvifClient,
    RevisionConflict,
    RtspProbe,
    WsDiscoveryClient,
    compile_plan,
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


class PromptsBody(BaseModel):
    """open-vocab 탐지 프롬프트 설정 요청."""

    classes: list[str] = Field(..., description="탐지할 텍스트 프롬프트 목록(학습 불필요)")
    enabled: bool | None = Field(default=None, description="설정과 함께 모델 활성화 여부")


class EnabledBody(BaseModel):
    enabled: bool


class CameraProfileBody(BaseModel):
    """카메라 탐지 프로파일 저장 요청(도메인 + 활성 이벤트)."""

    domain: str = Field(..., description="event_policies.yaml의 도메인 프로파일명")
    enabled_events: list[str] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=0)


class AlertPrefsBody(BaseModel):
    """알림 환경설정 저장 요청(이메일 임계값 + 이벤트별 발송 on/off)."""

    email_alert_level_threshold: str = Field(..., description="low/medium/high/critical")
    per_event_email_enabled: dict[str, bool] = Field(default_factory=dict)


class CameraProbeBody(BaseModel):
    rtsp_url: str = Field(min_length=1, max_length=2048)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=1024)
    verify_stream: bool = True


class CameraDraftProbeBody(CameraProbeBody):
    clear_credentials: bool = False

    @model_validator(mode="after")
    def valid_credentials(self) -> CameraDraftProbeBody:
        if self.clear_credentials and (self.username is not None or self.password is not None):
            raise ValueError("clear_credentials cannot be combined with username or password")
        return self


class CameraZonesBody(BaseModel):
    zones: list[CameraZone] = Field(default_factory=list, max_length=32)
    expected_revision: int | None = Field(default=None, ge=0)


class OnvifProfilesBody(BaseModel):
    discovery_id: str = Field(min_length=16, max_length=128)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=1024)


class CameraDiscoveryBody(BaseModel):
    protocol: Literal["onvif", "gateway", "all"] = "all"


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


class IncidentActionBody(BaseModel):
    """Single-admin incident action; AI event lifecycle remains immutable."""

    status: Literal["acknowledged", "in_progress", "resolved"]
    disposition: Literal["confirmed", "false_positive", "duplicate", "test"] | None = None
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def resolved_requires_disposition(self) -> IncidentActionBody:
        if self.status == "resolved" and self.disposition is None:
            raise ValueError("resolved incidents require a disposition")
        return self


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

    auth = [Depends(require_api_key)]
    onvif_discovery_lock = threading.RLock()
    onvif_discoveries: dict[str, tuple[float, str, int]] = {}

    def remember_onvif_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.monotonic()
        with onvif_discovery_lock:
            expired = [token for token, (expires_at, _, _) in onvif_discoveries.items() if expires_at <= now]
            for token in expired:
                del onvif_discoveries[token]
            remembered = []
            for device in devices:
                host = device.get("host")
                port = device.get("onvif_port", 80)
                if not isinstance(host, str) or not host or not isinstance(port, int) or not 1 <= port <= 65535:
                    continue
                token = secrets.token_urlsafe(24)
                onvif_discoveries[token] = (now + 120, host, port)
                remembered.append({**device, "discovery_id": token})
            return remembered

    def discovered_onvif_target(discovery_id: str) -> tuple[str, int]:
        now = time.monotonic()
        with onvif_discovery_lock:
            entry = onvif_discoveries.get(discovery_id)
            if entry is None or entry[0] <= now:
                onvif_discoveries.pop(discovery_id, None)
                raise HTTPException(status_code=400, detail="카메라 검색 결과가 만료되었습니다. 다시 검색하세요.")
            return entry[1], entry[2]

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

    def _serialized_camera_mutation(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            with app.state.camera_runtime_lock:
                return func(*args, **kwargs)

        return wrapped

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

    def _apply_camera_runtime(
        camera: CameraConfig,
        validated_addresses: list[str] | None = None,
    ) -> tuple[bool, str | None, bool, bool | None]:
        addresses = validated_addresses or _validate_camera_target(camera.rtsp_url)
        if not camera.enabled:
            return _remove_camera_runtime(camera.id)
        pipeline_obj = app.state.pipeline
        credential = app.state.camera_credentials.get(camera.credential_ref) or {}
        target_scope = CredentialStore.target_scope(
            addresses,
            urlsplit(camera.rtsp_url).port or 554,
        )
        stored_scope = credential.get("target_scope")
        if stored_scope and stored_scope != target_scope:
            return False, "credential_target_changed", False, None
        if credential and not stored_scope:
            return False, "credential_target_unverified", False, None
        # The persisted URL stays hostname-based. The mounted capture uses a
        # validated address so later DNS rebinding cannot redirect it.
        stream_url = RtspProbe.pin_url(
            RtspProbe.with_credentials(
                camera.rtsp_url,
                credential.get("username"),
                credential.get("password"),
            ),
            addresses[0],
        )
        zones = {
            zone.id: {
                "type": zone.type,
                "points": [[point.x, point.y] for point in zone.points],
                "normalized": True,
                "direction": zone.direction,
            }
            for zone in camera.zones
        }
        profile = {
            "domain": camera.profile or "general",
            "enabled_events": list(camera.enabled_events),
            "events": list(camera.enabled_events),
            "zones": zones,
            "detection_type": camera.profile or "general",
        }
        pipeline_applied = False
        pipeline_error = None
        if pipeline_obj is None or not hasattr(pipeline_obj, "apply_camera"):
            pipeline_error = "pipeline_unavailable"
        else:
            try:
                pipeline_obj.apply_camera(camera.id, stream_url, profile=profile,
                                          target_fps=camera.fps, resize=camera.resolution)
                pipeline_applied = True
            except Exception as exc:
                pipeline_error = type(exc).__name__
        gateway_synced: bool | None = None
        gateway_error = None
        if app.state.mediamtx is not None:
            try:
                app.state.mediamtx.sync(camera.id, stream_url)
                gateway_synced = True
            except MediaMTXError as exc:
                gateway_synced = False
                gateway_error = str(exc)
            except Exception as exc:
                gateway_synced = False
                gateway_error = type(exc).__name__
        errors = ([pipeline_error] if pipeline_error else []) + (
            [f"gateway:{gateway_error}"] if gateway_error else []
        )
        applied = pipeline_applied and gateway_synced is not False
        return applied, ";".join(errors) or None, pipeline_applied, gateway_synced

    def _validate_camera_target(rtsp_url: str) -> list[str]:
        parsed = urlsplit(rtsp_url)
        try:
            addresses = app.state.rtsp_probe.allowed_host(parsed.hostname or "", parsed.port or 554)
        except OSError as exc:
            raise HTTPException(status_code=422, detail="RTSP host could not be resolved") from exc
        if not addresses:
            raise HTTPException(
                status_code=422,
                detail="RTSP target must resolve only to private LAN addresses",
            )
        return addresses

    def _camera_credentials_for_target(
        camera: CameraConfig,
        body: CameraInput | CameraDraftProbeBody,
        addresses: list[str],
    ) -> tuple[str | None, str | None]:
        if body.clear_credentials:
            return None, None
        stored = app.state.camera_credentials.get(camera.credential_ref) or {}
        current = urlsplit(camera.rtsp_url)
        target = urlsplit(body.rtsp_url)
        current_scope = (current.hostname or "", current.port or 554)
        target_scope = (target.hostname or "", target.port or 554)
        resolved_scope = CredentialStore.target_scope(addresses, target.port or 554)
        stored_scope = stored.get("target_scope")
        if (
            current_scope != target_scope
            or (stored_scope and stored_scope != resolved_scope)
            or (stored and not stored_scope)
        ):
            reused = [
                field
                for field in ("username", "password")
                if getattr(body, field) is None and stored.get(field)
            ]
            if reused:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "새 RTSP 장비 또는 포트에는 기존 자격 증명을 재사용할 수 없습니다. "
                        "교체 자격 증명을 입력하거나 인증 정보 제거를 선택하세요."
                    ),
                )
        return (
            body.username if body.username is not None else stored.get("username"),
            body.password if body.password is not None else stored.get("password"),
        )

    def _remove_camera_runtime(camera_id: str) -> tuple[bool, str | None, bool, bool | None]:
        pipeline_obj = app.state.pipeline
        pipeline_removed = False
        pipeline_error = None
        if pipeline_obj is not None and hasattr(pipeline_obj, "remove_camera"):
            try:
                pipeline_removed = bool(pipeline_obj.remove_camera(camera_id))
            except Exception as exc:
                pipeline_error = type(exc).__name__
        gateway_synced: bool | None = None
        gateway_error = None
        if app.state.mediamtx is not None:
            try:
                app.state.mediamtx.delete(camera_id)
                gateway_synced = True
            except MediaMTXError as exc:
                gateway_synced = False
                gateway_error = str(exc)
            except Exception as exc:
                gateway_synced = False
                gateway_error = type(exc).__name__
        errors = ([pipeline_error] if pipeline_error else []) + (
            [f"gateway:{gateway_error}"] if gateway_error else []
        )
        return (not errors, ";".join(errors) or None,
                pipeline_removed, gateway_synced)

    def _invalidate_commissioning_acceptance() -> None:
        app.state.commissioning.invalidate_completion()

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

    # ── 이벤트 ───────────────────────────────────────────────────────────
    @app.get("/api/events", tags=["events"], dependencies=auth)
    def events(
        limit: int = Query(100, ge=1, le=service.EVENTS_MAX_LIMIT),
        event_type: str | None = Query(None),
        camera_id: str | None = Query(None),
        alert_level: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        items = service.list_events(
            limit=limit,
            event_type=event_type,
            camera_id=camera_id,
            alert_level=alert_level,
            since=since,
            until=until,
            offset=offset,
            event_repository=app.state.event_repository,
            durable_store_status=app.state.durable_event_store_status,
        )
        return {"count": len(items), "events": items}

    @app.get("/api/incidents", tags=["incidents"], dependencies=auth)
    def incidents(
        status: Literal["new", "acknowledged", "in_progress", "resolved"] | None = Query(None),
        category: Literal["safety", "camera_health"] | None = Query(None),
        unresolved: bool = Query(False),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, Any]:
        if status is not None and unresolved:
            raise HTTPException(status_code=400, detail="status와 unresolved는 함께 사용할 수 없습니다.")
        store = app.state.incident_store
        if store is None:
            return {"available": False, "count": 0, "incidents": []}
        items = store.list(status=status, category=category, unresolved=unresolved, limit=limit)
        missing_alert_levels = [
            str(item["event_id"])
            for item in items
            if item.get("source_event_alert_level") is None and item.get("event_id")
        ]
        event_repository = app.state.event_repository
        if missing_alert_levels and hasattr(event_repository, "get_alert_levels"):
            try:
                alert_levels = event_repository.get_alert_levels(missing_alert_levels)
                if app.state.durable_event_store_status is not None:
                    app.state.durable_event_store_status.record_success(operation="query:alert_levels")
            except Exception as error:
                if app.state.durable_event_store_status is not None:
                    app.state.durable_event_store_status.record_failure(error, operation="query:alert_levels")
                alert_levels = {}
            if alert_levels:
                store.backfill_source_alert_levels(alert_levels)
                for item in items:
                    if item.get("source_event_alert_level") is None:
                        item["source_event_alert_level"] = alert_levels.get(str(item["event_id"]))
        return {"available": True, "count": len(items), "incidents": items}

    @app.post("/api/incidents/{event_id}/actions", tags=["incidents"], dependencies=auth)
    def incident_action(event_id: str, body: IncidentActionBody) -> dict[str, Any]:
        if not event_id or len(event_id) > 128 or any(character in event_id for character in "\r\n"):
            raise HTTPException(status_code=400, detail="유효하지 않은 이벤트 ID입니다.")
        store = app.state.incident_store
        if store is None:
            raise HTTPException(status_code=503, detail="사건 저장소가 연결되지 않았습니다.")
        try:
            event = service.get_event(
                event_id,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            )
        except service.EventLookupUnavailable as error:
            raise HTTPException(status_code=503, detail="이벤트 저장소를 조회할 수 없습니다.") from error
        if event is None:
            raise HTTPException(status_code=404, detail="이벤트 증거를 찾을 수 없습니다.")
        try:
            incident = store.act(
                event_id,
                status=body.status,
                disposition=body.disposition,
                note=body.note.strip(),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        # Notes can contain sensitive operational context, so the signed admin audit
        # only records the state transition and durable event reference.
        service.write_audit(
            "incident_action",
            incident_event_id=event_id,
            incident_status=body.status,
            incident_disposition=body.disposition,
        )
        return incident

    @app.get("/api/incidents/{event_id}", tags=["incidents"], dependencies=auth)
    def incident_detail(event_id: str) -> dict[str, Any]:
        store = app.state.incident_store
        if store is None:
            raise HTTPException(status_code=503, detail="사건 저장소가 연결되지 않았습니다.")
        incident = store.get(event_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="사건 처리 기록이 없습니다.")
        return {"incident": incident, "actions": store.actions(event_id)}

    @app.get("/api/observations", tags=["observations"], dependencies=auth)
    def observations(
        camera_id: str | None = Query(None, max_length=64),
        limit: int = Query(120, ge=1, le=2000),
    ) -> dict[str, Any]:
        pipeline = app.state.pipeline
        if pipeline is None or not hasattr(pipeline, "get_observations"):
            return {"connected": False, "count": 0, "observations": []}
        items = pipeline.get_observations(camera_id=camera_id, limit=limit)
        return {"connected": True, "count": len(items), "observations": items}

    # ── 이벤트 실시간 스트림(SSE) — durable SQLite cursor 우선 ───────────────
    @app.get("/api/events/stream", tags=["events"], dependencies=auth)
    async def events_stream(request: Request) -> StreamingResponse:
        # EventSource 표준 재연결 cursor. SQLite가 있으면 monotonic stream_id를 사용하고,
        # legacy deployments only fall back to alert-log event IDs.
        last_event_id = request.headers.get("last-event-id")
        return StreamingResponse(
            service.stream_events(
                request.is_disconnected,
                last_event_id=last_event_id,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # nginx 등 역프록시 버퍼링 비활성(즉시 전달)
            },
        )

    # Register the literal stream path first so it cannot be interpreted as an event ID.
    @app.get("/api/events/{event_id}", tags=["events"], dependencies=auth)
    def event_detail(event_id: str) -> dict[str, Any]:
        if not event_id or len(event_id) > 128 or any(character in event_id for character in "\r\n"):
            raise HTTPException(status_code=400, detail="유효하지 않은 이벤트 ID입니다.")
        try:
            event = service.get_event(
                event_id,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            )
        except service.EventLookupUnavailable as error:
            raise HTTPException(status_code=503, detail="이벤트 저장소를 조회할 수 없습니다.") from error
        if event is None:
            raise HTTPException(status_code=404, detail="이벤트 증거를 찾을 수 없습니다.")
        return {"event": event}

    # ── 이벤트 실시간 스트림(WebSocket) — 양방향, 향후 프레임 푸시 확장 ─────
    @app.websocket("/ws/events")
    async def events_ws(websocket: WebSocket):
        """이벤트 실시간 WebSocket 스트림.

        SSE와 동일하게 durable SQLite(legacy에서는 alerts.log)의 신규 이벤트를 push. 연결 시 즉시 최근 이벤트 목록을
        전송하고, 이후 신규 이벤트를 JSON 프레임으로 push. 클라이언트가 ping을내면
        pong으로 응답(keepalive). WS는 Depends 사용이 제한적이므로 핸드셰이크의
        X-API-Key 헤더를 수동 검증한다. 브라우저 클라이언트는 인증된 SSE를 사용한다.
        """

        def _is_disconnected() -> bool:
            return websocket.client_state.name == "DISCONNECTED"

        async def _is_disconnected_async() -> bool:
            return _is_disconnected()

        api_key = websocket.headers.get("x-api-key")
        if not check_api_key(api_key):
            await websocket.close(code=4401, reason="인증 필요")
            return

        await websocket.accept()

        requested_cursor = websocket.headers.get("last-event-id") or websocket.query_params.get("cursor")
        cursor: int | None = None
        reconciliation_reason: str | None = None
        if app.state.event_repository is not None:
            try:
                # Snapshot before list_events closes the initial replay/subscription gap.
                cursor, reconciliation_reason = service.prepare_durable_event_cursor(
                    app.state.event_repository,
                    requested_cursor,
                    app.state.durable_event_store_status,
                )
            except Exception:
                reconciliation_reason = "durable-store-unavailable"

        # 연결 즉시 최근 이벤트 20건 전송(초기 로드)
        try:
            # A reconnect with a valid durable cursor must replay transitions in order. Sending
            # the current snapshot first can otherwise overwrite confirmed state with a replayed
            # provisional record that has the same event_id.
            recent = [] if requested_cursor and reconciliation_reason is None else service.list_events(
                limit=20,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            )
            initial: dict[str, Any] = {"type": "initial", "events": recent}
            if cursor is not None:
                initial["cursor"] = cursor
            await websocket.send_json(initial)

            # 이후 durable cursor로 신규 이벤트 push
            async for frame in service.stream_events_ws(
                _is_disconnected_async,
                event_repository=app.state.event_repository,
                cursor=cursor,
                reconciliation_reason=reconciliation_reason,
                durable_store_status=app.state.durable_event_store_status,
            ):
                if _is_disconnected():
                    break
                # ping/pong keepalive
                if frame.get("type") == "heartbeat" and not _is_disconnected():
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json(frame)
        except WebSocketDisconnect:
            return
        except Exception:
            return

    # ── 카메라 ───────────────────────────────────────────────────────────
    @app.get("/api/cameras", tags=["cameras"], dependencies=auth)
    def cameras() -> dict[str, Any]:
        revision, installed = app.state.camera_repository.list()
        merged = {str(item.get("id")): item for item in service.list_cameras(app.state.config)}
        for camera in installed:
            merged[camera.id] = camera.public_dict()
        items = sorted(merged.values(), key=lambda item: str(item.get("id", "")))
        return {"count": len(items), "cameras": items, "revision": revision}

    @app.post("/api/cameras", tags=["cameras"], dependencies=auth, status_code=201)
    @_serialized_camera_mutation
    def create_camera(body: CameraInput, expected_revision: int | None = Query(None, ge=0)) -> dict[str, Any]:
        repo = app.state.camera_repository
        domain = body.profile or "general"
        try:
            enabled_events = service.validate_event_selection(domain, body.enabled_events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        camera = CameraConfig.from_input(body.model_copy(update={
            "profile": domain,
            "enabled_events": enabled_events,
        }))
        zone_warnings = service.event_zone_warnings(enabled_events, list(camera.zones))
        if zone_warnings:
            raise HTTPException(
                status_code=400,
                detail="영역 설정이 필요한 감지 항목이 있습니다: " + "; ".join(zone_warnings),
            )
        addresses = _validate_camera_target(camera.rtsp_url)
        if camera.enabled:
            stream_test = app.state.rtsp_probe.run(
                camera.rtsp_url,
                username=body.username,
                password=body.password,
                verify_stream=True,
                validated_addresses=addresses,
            )
            if not (stream_test.get("ok") or stream_test.get("connected")):
                raise HTTPException(
                    status_code=400,
                    detail="카메라 등록 전 첫 프레임 검사가 필요합니다. 스트림을 확인한 뒤 다시 시도하세요.",
                )
        _, managed = repo.list()
        legacy_ids = {
            str(item.get("id"))
            for item in app.state.config.load_cameras()
            if isinstance(item, dict) and item.get("id")
        }
        if len(legacy_ids | {item.id for item in managed} | {camera.id}) > repo.MAX_CAMERAS:
            raise HTTPException(
                status_code=409,
                detail=f"total camera limit reached ({repo.MAX_CAMERAS})",
            )
        ref = f"camera:{camera.id}" if not body.clear_credentials and (body.username is not None or body.password is not None) else None
        previous_credential = app.state.camera_credentials.get(ref)
        if ref:
            app.state.camera_credentials.put(
                ref,
                body.username,
                body.password,
                CredentialStore.target_scope(addresses, urlsplit(camera.rtsp_url).port or 554),
            )
            camera = camera.model_copy(update={"credential_ref": ref})

        def restore_new_credential() -> None:
            if not ref:
                return
            if previous_credential is None:
                app.state.camera_credentials.delete(ref)
            else:
                app.state.camera_credentials.put(
                    ref,
                    previous_credential.get("username"),
                    previous_credential.get("password"),
                    previous_credential.get("target_scope"),
                )

        try:
            revision = repo.create(camera, expected_revision, occupied_ids=legacy_ids)
        except RevisionConflict as exc:
            restore_new_credential()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CameraAlreadyExists as exc:
            restore_new_credential()
            raise HTTPException(status_code=409, detail="camera already exists") from exc
        except CameraLimitReached as exc:
            restore_new_credential()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        applied, apply_error, pipeline_applied, gateway_synced = (
            _apply_camera_runtime(camera, addresses) if camera.enabled else (False, None, False, None)
        )
        service.write_audit("camera_created", camera=camera.id, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        _invalidate_commissioning_acceptance()
        return {
            "revision": revision,
            "camera": camera.public_dict(),
            "applied": applied,
            "apply_error": apply_error,
            "pipeline_applied": pipeline_applied,
            "gateway_synced": gateway_synced,
        }

    @app.put("/api/cameras/{camera_id}", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def update_camera(camera_id: str, body: CameraInput, expected_revision: int | None = Query(None, ge=0)) -> dict[str, Any]:
        if body.id is not None and body.id != camera_id:
            raise HTTPException(status_code=400, detail="camera id does not match path")
        repo = app.state.camera_repository
        observed_revision, cameras_at_read = repo.list()
        old = next((camera for camera in cameras_at_read if camera.id == camera_id), None)
        if old is None:
            raise HTTPException(status_code=404, detail="camera not found")
        addresses = _validate_camera_target(body.rtsp_url)
        _camera_credentials_for_target(old, body, addresses)
        domain = body.profile or old.profile or "general"
        try:
            enabled_events = service.validate_event_selection(domain, body.enabled_events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ref = None if body.clear_credentials else old.credential_ref
        if not body.clear_credentials and (body.username is not None or body.password is not None):
            # The path is the authority for updates; an omitted body id must
            # never produce a shared camera:None credential reference.
            ref = f"camera:{camera_id}"
        previous_credential = app.state.camera_credentials.get(ref)
        if not body.clear_credentials and (body.username is not None or body.password is not None):
            app.state.camera_credentials.put(
                ref,
                body.username,
                body.password,
                CredentialStore.target_scope(addresses, urlsplit(body.rtsp_url).port or 554),
            )

        def restore_update_credential() -> None:
            if body.clear_credentials or (body.username is None and body.password is None):
                return
            if previous_credential is None:
                app.state.camera_credentials.delete(ref)
            else:
                app.state.camera_credentials.put(
                    ref,
                    previous_credential.get("username"),
                    previous_credential.get("password"),
                    previous_credential.get("target_scope"),
                )
        camera = CameraConfig.from_input(
            body.model_copy(update={"profile": domain, "enabled_events": enabled_events}),
            ref,
            camera_id=camera_id,
        )
        zone_warnings = service.event_zone_warnings(enabled_events, list(camera.zones))
        if zone_warnings:
            restore_update_credential()
            raise HTTPException(
                status_code=400,
                detail="영역 설정이 필요한 감지 항목이 있습니다: " + "; ".join(zone_warnings),
            )
        try:
            revision, _ = repo.save(
                camera,
                expected_revision if expected_revision is not None else observed_revision,
            )
        except RevisionConflict as exc:
            restore_update_credential()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if camera.enabled:
            applied, apply_error, pipeline_applied, gateway_synced = _apply_camera_runtime(
                camera,
                addresses,
            )
        else:
            applied, apply_error, pipeline_applied, gateway_synced = _remove_camera_runtime(camera_id)
        if not applied:
            try:
                rollback_revision, _ = repo.save(old, expected_revision=revision)
            except RevisionConflict as exc:
                service.write_audit(
                    "camera_update_rollback_conflict",
                    camera=camera_id,
                    apply_error=apply_error,
                )
                raise HTTPException(
                    status_code=409,
                    detail="새 설정 적용에 실패했고 다른 변경과 충돌해 자동 복구하지 못했습니다.",
                ) from exc
            restore_update_credential()
            if old.enabled:
                restored, restore_error, _, _ = _apply_camera_runtime(old)
            else:
                restored, restore_error, _, _ = _remove_camera_runtime(camera_id)
            service.write_audit(
                "camera_update_rolled_back",
                camera=camera_id,
                failed_revision=revision,
                rollback_revision=rollback_revision,
                restored=restored,
                apply_error=apply_error,
                restore_error=restore_error,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "새 연결 설정을 실행 환경에 적용하지 못해 이전 설정으로 복구했습니다."
                    if restored else
                    "새 연결 설정 적용과 이전 설정 자동 복구가 모두 실패했습니다. 진단이 필요합니다."
                ),
            )
        if old.credential_ref and old.credential_ref != ref:
            # Only remove a legacy/stale reference after persistence and runtime handling.
            app.state.camera_credentials.delete(old.credential_ref)
        _invalidate_commissioning_acceptance()
        service.write_audit("camera_updated", camera=camera_id, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        return {
            "revision": revision,
            "camera": camera.public_dict(),
            "applied": applied,
            "apply_error": apply_error,
            "pipeline_applied": pipeline_applied,
            "gateway_synced": gateway_synced,
        }

    @app.delete("/api/cameras/{camera_id}", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def delete_camera(camera_id: str, expected_revision: int | None = Query(None, ge=0)) -> dict[str, Any]:
        repo = app.state.camera_repository
        current_revision, _ = repo.list()
        if expected_revision is not None and expected_revision != current_revision:
            raise HTTPException(
                status_code=409,
                detail=f"expected revision {expected_revision}, current revision {current_revision}",
            )
        old = repo.get(camera_id)
        if old is None:
            raise HTTPException(status_code=404, detail="camera not found")
        removed, apply_error, pipeline_removed, gateway_synced = _remove_camera_runtime(camera_id)
        if apply_error:
            # Keep the record and credential so an operator can retry cleanup;
            # otherwise an orphaned gateway path could remain viewable forever.
            service.write_audit(
                "camera_delete_cleanup_failed",
                camera=camera_id,
                pipeline_removed=pipeline_removed,
                gateway_synced=gateway_synced,
                apply_error=apply_error,
            )
            raise HTTPException(status_code=503, detail=f"camera cleanup failed: {apply_error}")
        try:
            revision, old = repo.delete(camera_id, expected_revision)
        except RevisionConflict as exc:
            _apply_camera_runtime(old)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="camera not found") from exc
        app.state.camera_credentials.delete(old.credential_ref)
        _invalidate_commissioning_acceptance()
        service.write_audit("camera_deleted", camera=camera_id, removed=removed,
                            pipeline_removed=pipeline_removed, gateway_synced=gateway_synced,
                            apply_error=apply_error)
        return {"revision": revision, "deleted": camera_id, "removed": removed,
                "apply_error": apply_error, "pipeline_removed": pipeline_removed,
                "gateway_synced": gateway_synced}

    @app.post("/api/cameras/{camera_id}/test", tags=["cameras"], dependencies=auth)
    def test_camera(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        addresses = _validate_camera_target(camera.rtsp_url)
        credential = app.state.camera_credentials.get(camera.credential_ref) or {}
        target_scope = CredentialStore.target_scope(
            addresses,
            urlsplit(camera.rtsp_url).port or 554,
        )
        stored_scope = credential.get("target_scope")
        if stored_scope and stored_scope != target_scope:
            raise HTTPException(
                status_code=409,
                detail="카메라 네트워크 주소가 변경되었습니다. 자격 증명을 다시 확인하세요.",
            )
        if credential and not stored_scope:
            raise HTTPException(
                status_code=409,
                detail="저장된 자격 증명의 장비 확인이 필요합니다. 자격 증명을 다시 입력하세요.",
            )
        return app.state.rtsp_probe.run(
            camera.rtsp_url,
            username=credential.get("username"),
            password=credential.get("password"),
            verify_stream=True,
            validated_addresses=addresses,
        )

    @app.post("/api/cameras/{camera_id}/test-draft", tags=["cameras"], dependencies=auth)
    def test_camera_draft(camera_id: str, body: CameraDraftProbeBody) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        addresses = _validate_camera_target(body.rtsp_url)
        username, password = _camera_credentials_for_target(camera, body, addresses)
        return app.state.rtsp_probe.run(
            body.rtsp_url,
            username=username,
            password=password,
            verify_stream=body.verify_stream,
            validated_addresses=addresses,
        )

    @app.post("/api/cameras/test", tags=["cameras"], dependencies=auth)
    def test_camera_transient(body: CameraProbeBody) -> dict[str, Any]:
        return app.state.rtsp_probe.run(
            body.rtsp_url,
            username=body.username,
            password=body.password,
            verify_stream=body.verify_stream,
        )

    @app.post("/api/cameras/{camera_id}/apply", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def apply_camera(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        _validate_camera_target(camera.rtsp_url)
        if not camera.enabled:
            removed, apply_error, pipeline_removed, gateway_synced = _remove_camera_runtime(camera_id)
            service.write_audit("camera_applied", camera=camera_id, applied=False,
                                pipeline_removed=pipeline_removed, gateway_synced=gateway_synced)
            return {"camera_id": camera_id, "applied": False, "removed": removed, "disabled": True,
                    "apply_error": apply_error, "pipeline_removed": pipeline_removed,
                    "gateway_synced": gateway_synced}
        applied, apply_error, pipeline_applied, gateway_synced = _apply_camera_runtime(camera)
        service.write_audit("camera_applied", camera=camera_id, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        if not applied:
            raise HTTPException(status_code=503, detail=f"camera apply failed: {apply_error}")
        return {"camera_id": camera_id, "applied": True, "pipeline_applied": pipeline_applied,
                "gateway_synced": gateway_synced}

    def _set_camera_enabled(
        camera_id: str,
        enabled: bool,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        repo = app.state.camera_repository
        observed_revision, cameras_at_read = repo.list()
        camera = next((item for item in cameras_at_read if item.id == camera_id), None)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        updated = camera.model_copy(update={"enabled": enabled})
        try:
            revision, _ = repo.save(
                updated,
                expected_revision=(
                    expected_revision if expected_revision is not None else observed_revision
                ),
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if enabled:
            applied, apply_error, pipeline_applied, gateway_synced = _apply_camera_runtime(updated)
        else:
            applied, apply_error, pipeline_applied, gateway_synced = _remove_camera_runtime(camera_id)
        if not applied:
            try:
                rollback_revision, _ = repo.save(camera, expected_revision=revision)
            except RevisionConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail="활성 상태 변경 실패 후 다른 설정과 충돌해 자동 복구하지 못했습니다.",
                ) from exc
            if camera.enabled:
                restored, restore_error, _, _ = _apply_camera_runtime(camera)
            else:
                restored, restore_error, _, _ = _remove_camera_runtime(camera_id)
            service.write_audit(
                "camera_enabled_rolled_back",
                camera=camera_id,
                requested_enabled=enabled,
                rollback_revision=rollback_revision,
                restored=restored,
                apply_error=apply_error,
                restore_error=restore_error,
            )
            raise HTTPException(
                status_code=503,
                detail="카메라 상태를 변경하지 못해 이전 상태로 복구했습니다.",
            )
        service.write_audit("camera_enabled_set", camera=camera_id, enabled=enabled, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        _invalidate_commissioning_acceptance()
        return {
            "camera_id": camera_id,
            "enabled": enabled,
            "revision": revision,
            "applied": applied,
            "apply_error": apply_error,
            "pipeline_applied": pipeline_applied,
            "gateway_synced": gateway_synced,
        }

    @app.post("/api/cameras/{camera_id}/enable", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def enable_camera(
        camera_id: str,
        expected_revision: int | None = Query(None, ge=0),
    ) -> dict[str, Any]:
        return _set_camera_enabled(camera_id, True, expected_revision)

    @app.post("/api/cameras/{camera_id}/disable", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def disable_camera(
        camera_id: str,
        expected_revision: int | None = Query(None, ge=0),
    ) -> dict[str, Any]:
        return _set_camera_enabled(camera_id, False, expected_revision)

    @app.get("/api/cameras/readiness", tags=["cameras"], dependencies=auth)
    def cameras_readiness() -> dict[str, Any]:
        revision, configured = app.state.camera_repository.list()
        enabled = [c.id for c in configured if c.enabled]
        pipeline_obj = app.state.pipeline
        if pipeline_obj is None or not hasattr(pipeline_obj, "get_status"):
            return {"ready": bool(enabled), "revision": revision, "enabled_cameras": enabled}
        try:
            status = pipeline_obj.get_status() or {}
            telemetry = status.get("cameras", {}) if isinstance(status, dict) else {}
        except Exception:
            telemetry = {}

        def is_receiving(camera_id: str) -> bool:
            item = telemetry.get(camera_id)
            if not isinstance(item, dict):
                return False
            health_state = item.get("health_state")
            if health_state is not None:
                return health_state == "receiving"
            return bool(
                item.get("connected")
                and item.get("last_frame_time") is not None
                and not item.get("is_stale", True)
            )

        healthy = [
            camera_id for camera_id in enabled if is_receiving(camera_id)
        ]
        return {
            "ready": bool(enabled) and len(healthy) == len(enabled),
            "revision": revision,
            "enabled_cameras": enabled,
            "healthy_cameras": healthy,
        }

    @app.get("/api/cameras/health", tags=["cameras"], dependencies=auth)
    def cameras_health() -> dict[str, Any]:
        revision, configured = app.state.camera_repository.list()
        telemetry = {}
        if app.state.pipeline is not None and hasattr(app.state.pipeline, "get_status"):
            try:
                telemetry = (app.state.pipeline.get_status() or {}).get("cameras", {})
            except Exception:
                telemetry = {}
        return {
            "status": "ok",
            "revision": revision,
            "configured": len(configured),
            "enabled": sum(c.enabled for c in configured),
            "cameras": telemetry,
        }

    @app.get("/api/cameras/plan", tags=["cameras"], dependencies=auth)
    def cameras_plan() -> dict[str, Any]:
        revision, configured = app.state.camera_repository.list()
        return compile_plan(revision, configured).to_dict()

    @app.get("/api/cameras/telemetry", tags=["cameras"], dependencies=auth)
    def camera_telemetry() -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        pl = app.state.pipeline
        if pl is None or not hasattr(pl, "get_status"):
            return {
                "connected": False,
                "telemetry_status": "unavailable",
                "generated_at": generated_at,
                "cameras": {},
            }
        try:
            status = pl.get_status() or {}
            if not isinstance(status, dict):
                raise TypeError("pipeline telemetry snapshot is not a mapping")
            cams = status.get("cameras", {})
            if not isinstance(cams, dict):
                raise TypeError("pipeline telemetry cameras is not a mapping")
        except Exception:
            # 기존 구현은 get_status 실패를 connected=true/cameras={}로 위장했다. 이는
            # 운영자에게 "카메라 없음"으로 보이므로 source-level error를 명시한다.
            return {
                "connected": False,
                "telemetry_status": "error",
                "generated_at": generated_at,
                "cameras": {},
            }
        return {
            "connected": True,
            "telemetry_status": "ok",
            "generated_at": generated_at,
            "cameras": cams,
        }

    @app.get("/api/cameras/{camera_id}", tags=["cameras"], dependencies=auth)
    def get_camera(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        revision, _ = app.state.camera_repository.list()
        return {"revision": revision, "camera": camera.public_dict()}

    @app.get(
        "/api/cameras/{camera_id}/tracking",
        tags=["cameras"],
        dependencies=auth,
    )
    def camera_tracking(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        if not camera.enabled:
            raise HTTPException(
                status_code=409,
                detail="disabled camera has no live tracking data",
            )
        pipeline_obj = app.state.pipeline
        get_snapshot = getattr(pipeline_obj, "get_tracking_snapshot", None)
        if not callable(get_snapshot):
            raise HTTPException(status_code=503, detail="tracking unavailable")
        return get_snapshot(camera_id)

    @app.put("/api/cameras/{camera_id}/zones", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def set_camera_zones(camera_id: str, body: CameraZonesBody) -> dict[str, Any]:
        repo = app.state.camera_repository
        observed_revision, cameras_at_read = repo.list()
        camera = next((item for item in cameras_at_read if item.id == camera_id), None)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        updated = camera.model_copy(update={"zones": body.zones})
        zone_warnings = service.event_zone_warnings(updated.enabled_events, updated.zones)
        if zone_warnings:
            raise HTTPException(
                status_code=400,
                detail="영역 기반 감지가 활성화되어 필요한 영역을 삭제하거나 변경할 수 없습니다: " + "; ".join(zone_warnings),
            )
        try:
            revision, _ = repo.save(
                updated,
                (
                    body.expected_revision
                    if body.expected_revision is not None
                    else observed_revision
                ),
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        applied, apply_error, pipeline_applied, gateway_synced = (
            _apply_camera_runtime(updated) if updated.enabled else (False, None, False, None)
        )
        if updated.enabled and not applied:
            try:
                rollback_revision, _ = repo.save(camera, expected_revision=revision)
            except RevisionConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail="영역 적용 실패 후 다른 설정과 충돌해 자동 복구하지 못했습니다.",
                ) from exc
            restored, restore_error, _, _ = _apply_camera_runtime(camera)
            service.write_audit(
                "camera_zones_rolled_back",
                camera=camera_id,
                rollback_revision=rollback_revision,
                restored=restored,
                apply_error=apply_error,
                restore_error=restore_error,
            )
            raise HTTPException(
                status_code=503,
                detail="새 영역을 실행 환경에 적용하지 못해 이전 영역으로 복구했습니다.",
            )
        service.write_audit("camera_zones_updated", camera=camera_id, zone_count=len(body.zones),
                            applied=applied, pipeline_applied=pipeline_applied,
                            gateway_synced=gateway_synced, apply_error=apply_error)
        _invalidate_commissioning_acceptance()
        return {
            "revision": revision, "camera_id": camera_id, "zones": [z.model_dump() for z in body.zones],
            "applied": applied, "apply_error": apply_error,
            "pipeline_applied": pipeline_applied, "gateway_synced": gateway_synced,
        }

    @app.get("/api/cameras/{camera_id}/snapshot", tags=["cameras"], dependencies=auth)
    def camera_snapshot(camera_id: str) -> Response:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        if not camera.enabled:
            raise HTTPException(status_code=409, detail="disabled camera has no live snapshot")
        pl = app.state.pipeline
        if pl is not None and hasattr(pl, "get_camera_snapshot"):
            image = pl.get_camera_snapshot(camera_id)
            if isinstance(image, bytes):
                return Response(
                    content=image,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"},
                )
        raise HTTPException(status_code=503, detail="live snapshot is unavailable without a mounted pipeline provider")

    @app.get("/api/onvif/discover", tags=["onvif"], dependencies=auth)
    def onvif_discover() -> dict[str, Any]:
        return app.state.onvif.discover()

    @app.post("/api/cameras/discover", tags=["onvif"], dependencies=auth)
    def camera_discover(body: CameraDiscoveryBody) -> dict[str, Any]:
        def discover_onvif() -> dict[str, Any]:
            result = app.state.onvif.discover()
            discovered = [
                {**device, "source_type": "onvif"}
                for device in result.get("devices", [])
                if isinstance(device, dict)
            ]
            devices = remember_onvif_devices(discovered)
            return {**result, "devices": devices}

        def discover_gateway() -> dict[str, Any]:
            gateway = app.state.mediamtx
            ready_paths = getattr(gateway, "ready_paths", None)
            if not callable(ready_paths):
                return {"available": False, "devices": [], "reason": "영상 Gateway 검색을 사용할 수 없습니다."}
            try:
                paths = ready_paths()
            except MediaMTXError:
                return {"available": False, "devices": [], "reason": "영상 Gateway에 연결할 수 없습니다."}
            existing_ids = {camera.id for camera in app.state.camera_repository.list()[1]}
            rtsp_base = os.environ.get("KSI_CCTV_GATEWAY_RTSP_URL", "rtsp://mediamtx:8554").rstrip("/")
            devices = []
            for path in paths:
                if path["name"] in existing_ids:
                    continue
                video: dict[str, Any] = next((track for track in path.get("tracks", []) if track.get("codec")), {})
                dimensions = (
                    f"{video.get('width')}x{video.get('height')}"
                    if video.get("width") and video.get("height") else "해상도 확인 필요"
                )
                devices.append({
                    "name": f"연결된 영상 {path['name']}",
                    "host": "영상 Gateway",
                    "rtsp_url": f"{rtsp_base}/{path['name']}",
                    "model": f"{video.get('codec') or '영상'} · {dimensions}",
                    "source_type": "gateway",
                })
            return {"available": True, "devices": devices}

        if body.protocol == "onvif":
            return discover_onvif()
        if body.protocol == "gateway":
            return discover_gateway()

        onvif = discover_onvif()
        gateway = discover_gateway()
        devices = [*onvif["devices"], *gateway["devices"]]
        available = bool(onvif.get("available") or gateway.get("available"))
        reason = None if available else "; ".join(
            str(source["reason"])
            for source in (onvif, gateway)
            if source.get("reason")
        ) or "카메라 검색을 사용할 수 없습니다."
        return {
            "available": available,
            "devices": devices,
            "sources": {
                "onvif": {"available": bool(onvif.get("available")), "reason": onvif.get("reason")},
                "gateway": {"available": bool(gateway.get("available")), "reason": gateway.get("reason")},
            },
            **({"reason": reason} if reason else {}),
        }

    @app.get("/api/onvif/profiles", tags=["onvif"], dependencies=auth)
    def onvif_profiles(discovery_id: str = Query(..., min_length=16, max_length=128)) -> dict[str, Any]:
        host, port = discovered_onvif_target(discovery_id)
        try:
            addresses = app.state.rtsp_probe.allowed_host(host, port)
            if not addresses:
                raise HTTPException(status_code=400, detail="ONVIF host must resolve only to private LAN addresses")
        except OSError as exc:
            raise HTTPException(status_code=400, detail="ONVIF host could not be resolved") from exc
        return app.state.onvif.profiles(addresses[0], port)

    @app.post("/api/onvif/profiles", tags=["onvif"], dependencies=auth)
    def onvif_profiles_authenticated(body: OnvifProfilesBody) -> dict[str, Any]:
        host, port = discovered_onvif_target(body.discovery_id)
        try:
            addresses = app.state.rtsp_probe.allowed_host(host, port)
            if not addresses:
                raise HTTPException(status_code=400, detail="ONVIF host must resolve only to private LAN addresses")
        except OSError as exc:
            raise HTTPException(status_code=400, detail="ONVIF host could not be resolved") from exc
        return app.state.onvif.profiles(
            addresses[0],
            port,
            body.username,
            body.password,
        )

    # ── 카메라 탐지 프로파일(도메인·이벤트 편집) ─────────────────────────
    @app.get("/api/camera-profiles", tags=["cameras"], dependencies=auth)
    def camera_profiles() -> dict[str, Any]:
        return service.get_camera_profiles()

    @app.put("/api/cameras/{camera_id}/profile", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def set_camera_profile(camera_id: str, body: CameraProfileBody) -> dict[str, Any]:
        try:
            observed_revision, managed_cameras = app.state.camera_repository.list()
            managed = next((item for item in managed_cameras if item.id == camera_id), None)
            if managed is not None:
                enabled_events = service.validate_event_selection(
                    body.domain, body.enabled_events
                )
                zone_warnings = service.event_zone_warnings(
                    enabled_events, list(managed.zones)
                )
                if zone_warnings:
                    raise ValueError(
                        "영역 설정이 필요한 감지 항목이 있습니다: "
                        + "; ".join(zone_warnings)
                    )
                revision, updated = app.state.camera_repository.update_profile(
                    camera_id,
                    body.domain,
                    enabled_events,
                    (
                        body.expected_revision
                        if body.expected_revision is not None
                        else observed_revision
                    ),
                )
                applied, apply_error, pipeline_applied, gateway_synced = (
                    _apply_camera_runtime(updated) if updated and updated.enabled else (False, None, False, None)
                )
                if updated.enabled and not applied:
                    try:
                        rollback_revision, _ = app.state.camera_repository.save(
                            managed,
                            expected_revision=revision,
                        )
                    except RevisionConflict as exc:
                        raise HTTPException(
                            status_code=409,
                            detail="감지 설정 적용 실패 후 다른 설정과 충돌해 자동 복구하지 못했습니다.",
                        ) from exc
                    restored, restore_error, _, _ = _apply_camera_runtime(managed)
                    service.write_audit(
                        "camera_profile_rolled_back",
                        camera=camera_id,
                        rollback_revision=rollback_revision,
                        restored=restored,
                        apply_error=apply_error,
                        restore_error=restore_error,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="새 감지 설정을 실행 환경에 적용하지 못해 이전 설정으로 복구했습니다.",
                    )
                result = {
                    "camera_id": camera_id,
                    "domain": body.domain,
                    "enabled_events": enabled_events,
                    "revision": revision,
                    "applied": applied,
                    "apply_error": apply_error,
                    "pipeline_applied": pipeline_applied,
                    "gateway_synced": gateway_synced,
                    "configuration_warnings": service.event_zone_warnings(
                        enabled_events, list(updated.zones)
                    ),
                }
                _invalidate_commissioning_acceptance()
            else:
                result = service.set_camera_profile(camera_id, body.domain, body.enabled_events)
                pipeline = app.state.pipeline
                manager = getattr(pipeline, "camera_manager", None)
                mounted = (
                    manager.get_camera(camera_id)
                    if manager is not None and hasattr(manager, "get_camera")
                    else None
                )
                if mounted is not None and hasattr(pipeline, "set_camera_profile"):
                    current_profiles = getattr(pipeline, "_camera_profiles", {})
                    current = current_profiles.get(camera_id, {}) if isinstance(current_profiles, dict) else {}
                    runtime_profile = {
                        **(current if isinstance(current, dict) else {}),
                        "domain": body.domain,
                        "enabled_events": list(result["enabled_events"]),
                        "events": list(result["enabled_events"]),
                    }
                    pipeline.set_camera_profile(camera_id, runtime_profile)
                    result["applied"] = True
                    result["pipeline_applied"] = True
            service.write_audit(
                "camera_profile_set",
                camera=camera_id,
                domain=body.domain,
                event_count=len(result["enabled_events"]),
                applied=result.get("applied"),
                pipeline_applied=result.get("pipeline_applied"),
                gateway_synced=result.get("gateway_synced"),
                apply_error=result.get("apply_error"),
            )
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RevisionConflict as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except KeyError as e:
            raise HTTPException(status_code=404, detail="camera not found") from e

    # ── 모델 ─────────────────────────────────────────────────────────────
    @app.get("/api/models", tags=["models"], dependencies=auth)
    def models() -> dict[str, Any]:
        items = service.list_models()
        registry = getattr(app.state.pipeline, "model_registry", None)
        status_provider = getattr(registry, "to_status_dict", None)
        runtime = status_provider() if callable(status_provider) else {}
        for item in items:
            status = runtime.get(item["name"], {}) if isinstance(runtime, dict) else {}
            item["runtime"] = {
                "available": registry is not None,
                "loaded": status.get("loaded"),
                "error": status.get("error"),
                "inference_count": status.get("inference_count"),
                "error_count": status.get("error_count"),
                "last_latency_ms": status.get("last_latency_ms"),
            }
        return {"count": len(items), "models": items}

    # ── 정책 ─────────────────────────────────────────────────────────────
    @app.get("/api/policies", tags=["policies"], dependencies=auth)
    def policies() -> dict[str, Any]:
        return service.list_policies()

    # ── 이벤트 카탈로그(SSOT, 카메라별 자유 선택용) ──────────────────────────
    @app.get("/api/event-catalog", tags=["policies"], dependencies=auth)
    def event_catalog() -> dict[str, Any]:
        return _runtime_event_catalog()

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

    # ── 설정(마스킹된 큐레이션 뷰) ───────────────────────────────────────
    @app.get("/api/config", tags=["config"], dependencies=auth)
    def config_view() -> dict[str, Any]:
        return service.config_sanitized(app.state.config)

    # ── 시스템 상태(리소스 메트릭 + 헬스 컴포넌트) ───────────────────────
    @app.get("/api/system", tags=["system"], dependencies=auth)
    def system_info() -> dict[str, Any]:
        return {
            "metrics": service.get_system_metrics(),
            "health": _build_checker().run_checks().to_dict(),
            "ai_runtime": (
                app.state.pipeline.get_ai_runtime_status()
                if app.state.pipeline is not None and hasattr(app.state.pipeline, "get_ai_runtime_status")
                else {}
            ),
        }

    # ── 알림 환경설정(읽기/저장) ─────────────────────────────────────────
    @app.get("/api/alerts/status", tags=["settings"], dependencies=auth)
    def alert_delivery_status() -> dict[str, Any]:
        manager = app.state.alert_manager
        if manager is None:
            return {"available": False}
        return {"available": True, **manager.notification_status()}

    @app.get("/api/alert-preferences", tags=["settings"], dependencies=auth)
    def alert_prefs_get() -> dict[str, Any]:
        return service.get_alert_preferences()

    @app.put("/api/alert-preferences", tags=["settings"], dependencies=auth)
    def alert_prefs_put(body: AlertPrefsBody) -> dict[str, Any]:
        try:
            result = service.set_alert_preferences(
                body.email_alert_level_threshold, body.per_event_email_enabled
            )
            service.write_audit(
                "alert_preferences_saved",
                threshold=body.email_alert_level_threshold,
                enabled_count=result["enabled_count"],
            )
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ── 캡처 이미지 갤러리(이벤트 캡처 — PII라 인증 필수) ─────────────────
    @app.get("/api/captures", tags=["captures"], dependencies=auth)
    def captures_list() -> dict[str, Any]:
        return service.list_captures()

    @app.get("/api/captures/{date}/{camera}/{name}", tags=["captures"], dependencies=auth)
    def capture_image(date: str, camera: str, name: str) -> Response:
        data = service.read_capture_image(date, camera, name)
        if data is None:
            raise HTTPException(status_code=404, detail="캡처를 찾을 수 없거나 접근할 수 없습니다.")
        return Response(
            content=data,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    # ── 감사 로그(관리 작업 추적 — 인증) ─────────────────────────────────
    @app.get("/api/audit", tags=["audit"], dependencies=auth)
    def audit_list(limit: int = Query(200, ge=1, le=1000)) -> dict[str, Any]:
        items = service.list_audit(limit=limit)
        return {"count": len(items), "entries": items}

    # ── 쓰기: open-vocab 프롬프트 설정(학습 없이 탐지 대상 변경) ──────────
    @app.post("/api/models/{name}/prompts", tags=["models"], dependencies=auth)
    def set_prompts(name: str, body: PromptsBody) -> dict[str, Any]:
        try:
            result = service.set_model_prompts(body.classes, model_name=name, enabled=body.enabled)
            service.write_audit(
                "model_prompts_set", model=name, count=len(result.get("classes", []))
            )
            return result
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ── 쓰기: 모델 활성/비활성 ───────────────────────────────────────────
    @app.put("/api/models/{name}/enabled", tags=["models"], dependencies=auth)
    def set_enabled(name: str, body: EnabledBody) -> dict[str, Any]:
        try:
            result = service.set_model_enabled(name, body.enabled)
            service.write_audit("model_enabled_set", model=name, enabled=bool(body.enabled))
            return result
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    # ── 쓰기: 업로드 모델 삭제(기본 모델은 보호) ─────────────────────────
    @app.delete("/api/models/{name}", tags=["models"], dependencies=auth)
    def delete_model(name: str) -> dict[str, Any]:
        try:
            result = service.delete_uploaded_model(name)
            service.write_audit("model_deleted", model=name)
            return result
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ── 쓰기: 사용자 ONNX 모델 업로드(검증 샌드박스 후 disabled 등록) ──────────
    @app.post("/api/models/upload", tags=["models"], dependencies=auth)
    async def upload_model(
        file: UploadFile = File(...),
        model_name: str = Form("uploaded model"),
        classes: str = Form(""),  # 쉼표 구분
        input_height: int = Form(640),
        input_width: int = Form(640),
    ) -> dict[str, Any]:
        from src.models import upload_validator

        max_bytes = upload_validator.MAX_FILE_SIZE_BYTES
        temporary_path: Path | None = None
        try:
            temporary_dir = upload_validator.UPLOAD_BASE_DIR / "tmp" / "api"
            temporary_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(temporary_dir, 0o700)
            descriptor, raw_path = tempfile.mkstemp(prefix="upload-", suffix=".onnx", dir=temporary_dir)
            temporary_path = Path(raw_path)
            total = 0
            with os.fdopen(descriptor, "wb") as destination:
                os.chmod(temporary_path, 0o600)
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail=f"파일이 너무 큽니다(최대 {max_bytes // (1024 * 1024)}MB).")
                    destination.write(chunk)
            result = service.handle_model_upload(
                file_path=temporary_path,
                original_filename=file.filename or "uploaded.onnx",
                model_name=model_name,
                classes=[c.strip() for c in classes.split(",") if c.strip()],
                input_size=[int(input_height), int(input_width)],
            )
        finally:
            await file.close()
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        if not result.get("success"):
            service.write_audit(
                "model_upload_failed",
                original_filename=file.filename or "",
                error=str(result.get("error", "")),
            )
            raise HTTPException(status_code=400, detail=result.get("error", "업로드 실패"))
        service.write_audit(
            "model_upload",
            profile_name=result.get("profile_name", ""),
            model_name=model_name,
            original_filename=file.filename or "",
        )
        return result

    return app


app = create_app()
