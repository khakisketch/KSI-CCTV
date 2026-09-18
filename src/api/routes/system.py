"""시스템/플랫폼 그룹 라우트 (U12a 분할). 경로·동작 무변경 이동."""
from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.api import service


class PromptsBody(BaseModel):
    """open-vocab 탐지 프롬프트 설정 요청."""

    classes: list[str] = Field(..., description="탐지할 텍스트 프롬프트 목록(학습 불필요)")
    enabled: bool | None = Field(default=None, description="설정과 함께 모델 활성화 여부")


class EnabledBody(BaseModel):
    enabled: bool


class AlertPrefsBody(BaseModel):
    """알림 환경설정 저장 요청(이메일 임계값 + 이벤트별 발송 on/off)."""

    email_alert_level_threshold: str = Field(..., description="low/medium/high/critical")
    per_event_email_enabled: dict[str, bool] = Field(default_factory=dict)


class AuditEntryBody(BaseModel):
    """콘솔 감사 기록 요청. actor는 ContextVar만 사용 — body actor 키는 무시된다."""

    event: Any = Field(default=None, description="감사 이벤트명(^[a-z0-9_]{1,64}$)")
    fields: Any = Field(default=None, description="감사 필드(문자열 키 객체, 직렬화 ≤4096B)")

    model_config = {"extra": "ignore"}


_AUDIT_EVENT_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_AUDIT_FORBIDDEN_KEYS = frozenset({"password", "passwordhash", "salt", "token", "secret"})
# write_audit(event, **fields) 호출과 충돌하는 예약 키(경로 주입·TypeError 방지).
_AUDIT_RESERVED_KEYS = frozenset({"audit_log", "event"})
# 스푸핑 방지: ContextVar actor만 기록하므로 body의 actor 관련 키는 버린다(무시 선택).
_AUDIT_ACTOR_KEYS = frozenset({"actor", "actortype"})
_AUDIT_MAX_FIELDS_BYTES = 4096


def _audit_normalize_key(key: str) -> str:
    """금지/actor 키 비교용 정규화(대소문자·`_`·`-` 무시)."""
    return key.lower().replace("_", "").replace("-", "")


def register(
    app,
    auth,
    *,
    build_checker: Callable[[], Any],
    runtime_event_catalog: Callable[[], dict[str, Any]],
) -> None:
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
        return runtime_event_catalog()

    # ── 설정(마스킹된 큐레이션 뷰) ───────────────────────────────────────
    @app.get("/api/config", tags=["config"], dependencies=auth)
    def config_view() -> dict[str, Any]:
        return service.config_sanitized(app.state.config)

    # ── 시스템 상태(리소스 메트릭 + 헬스 컴포넌트) ───────────────────────
    @app.get("/api/system", tags=["system"], dependencies=auth)
    def system_info() -> dict[str, Any]:
        return {
            "metrics": service.get_system_metrics(),
            "health": build_checker().run_checks().to_dict(),
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

    # ── 감사 기록(콘솔 사용자 관리 추적 — 인증, 콘솔 서버사이드 내부용) ───────
    @app.post("/api/audit/entry", tags=["audit"], dependencies=auth)
    def audit_entry(body: AuditEntryBody) -> dict[str, Any]:
        if not isinstance(body.event, str) or _AUDIT_EVENT_RE.match(body.event) is None:
            raise HTTPException(status_code=400, detail="event는 ^[a-z0-9_]{1,64}$ 형식이어야 합니다.")
        fields = body.fields if body.fields is not None else {}
        if not isinstance(fields, dict):
            raise HTTPException(status_code=400, detail="fields는 객체여야 합니다.")
        for key in fields:
            if not isinstance(key, str):
                raise HTTPException(status_code=400, detail="fields 키는 문자열이어야 합니다.")
        for key, value in fields.items():
            if _audit_normalize_key(key) in _AUDIT_FORBIDDEN_KEYS:
                raise HTTPException(status_code=400, detail=f"fields에 금지된 키가 있습니다: {key}")
            if isinstance(value, dict):
                for nested in value:
                    if isinstance(nested, str) and _audit_normalize_key(nested) in _AUDIT_FORBIDDEN_KEYS:
                        raise HTTPException(
                            status_code=400, detail=f"fields에 금지된 키가 있습니다: {key}.{nested}"
                        )
        try:
            raw = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as e:
            raise HTTPException(status_code=400, detail="fields는 JSON 직렬화 가능해야 합니다.") from e
        if len(raw.encode("utf-8")) > _AUDIT_MAX_FIELDS_BYTES:
            raise HTTPException(status_code=400, detail="fields 크기가 4096B를 초과합니다.")
        safe = {
            key: value
            for key, value in fields.items()
            if key not in _AUDIT_RESERVED_KEYS and _audit_normalize_key(key) not in _AUDIT_ACTOR_KEYS
        }
        if not service.write_audit(body.event, **safe):
            raise HTTPException(status_code=503, detail="감사 기록에 실패했습니다.")
        return {"ok": True}

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
