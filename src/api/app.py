"""KSI-CCTV 엔진 API (FastAPI) — REST 엔드포인트.

읽기 엔드포인트(events/cameras/models/policies/config/health)를 제공한다. 제어(start/stop)·
실시간 스트림(SSE/WS)은 데몬 통합 단계에서 추가한다(P1 범위는 읽기 + 인증 + 마스킹).

실행: uvicorn src.api.app:app  또는  create_app() (테스트). 보호 엔드포인트는 X-API-Key 필요.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from src.api import service
from src.api.auth import require_api_key
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


def create_app(config: Any | None = None, pipeline: Any | None = None) -> FastAPI:
    """엔진 API 앱 생성. config 미지정 시 AppConfig() 로드. pipeline 지정 시 파이프라인 헬스 포함."""
    app = FastAPI(
        title="KSI-CCTV Engine API",
        version="1.0.0",
        description="설치형 AI CCTV 엔진 API — 이벤트·카메라·모델·정책·설정·헬스(읽기, 인증 필요)",
    )
    app.state.config = config or AppConfig()
    app.state.pipeline = pipeline

    auth = [Depends(require_api_key)]

    # ── 헬스 ─────────────────────────────────────────────────────────────
    @app.get("/api/health/live", tags=["health"])
    def health_live() -> dict[str, str]:
        """liveness 프로브 — 프로세스 생존 시 항상 200(비인증, 외부 모니터링/컨테이너용)."""
        return {"status": "alive"}

    @app.get("/api/health", tags=["health"], dependencies=auth)
    def health() -> dict[str, Any]:
        checker = HealthChecker()
        checker.register("disk_space", check_disk_space)
        checker.register("memory_usage", check_memory_usage)
        if app.state.pipeline is not None:
            register_pipeline_health_checks(checker, app.state.pipeline)
        return checker.run_checks().to_dict()

    # ── 이벤트 ───────────────────────────────────────────────────────────
    @app.get("/api/events", tags=["events"], dependencies=auth)
    def events(
        limit: int = Query(100, ge=1, le=service.EVENTS_MAX_LIMIT),
        event_type: str | None = Query(None),
        camera_id: str | None = Query(None),
    ) -> dict[str, Any]:
        items = service.list_events(limit=limit, event_type=event_type, camera_id=camera_id)
        return {"count": len(items), "events": items}

    # ── 카메라 ───────────────────────────────────────────────────────────
    @app.get("/api/cameras", tags=["cameras"], dependencies=auth)
    def cameras() -> dict[str, Any]:
        items = service.list_cameras(app.state.config)
        return {"count": len(items), "cameras": items}

    # ── 모델 ─────────────────────────────────────────────────────────────
    @app.get("/api/models", tags=["models"], dependencies=auth)
    def models() -> dict[str, Any]:
        items = service.list_models()
        return {"count": len(items), "models": items}

    # ── 정책 ─────────────────────────────────────────────────────────────
    @app.get("/api/policies", tags=["policies"], dependencies=auth)
    def policies() -> dict[str, Any]:
        return service.list_policies()

    # ── 설정(마스킹된 큐레이션 뷰) ───────────────────────────────────────
    @app.get("/api/config", tags=["config"], dependencies=auth)
    def config_view() -> dict[str, Any]:
        return service.config_sanitized(app.state.config)

    # ── 쓰기: open-vocab 프롬프트 설정(학습 없이 탐지 대상 변경) ──────────
    @app.post("/api/models/{name}/prompts", tags=["models"], dependencies=auth)
    def set_prompts(name: str, body: PromptsBody) -> dict[str, Any]:
        try:
            return service.set_model_prompts(body.classes, model_name=name, enabled=body.enabled)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    # ── 쓰기: 모델 활성/비활성 ───────────────────────────────────────────
    @app.put("/api/models/{name}/enabled", tags=["models"], dependencies=auth)
    def set_enabled(name: str, body: EnabledBody) -> dict[str, Any]:
        try:
            return service.set_model_enabled(name, body.enabled)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    return app


app = create_app()
