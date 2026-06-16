"""KSI-CCTV 엔진 API (FastAPI) — REST 엔드포인트.

읽기 엔드포인트(events/cameras/models/policies/config/health)를 제공한다. 제어(start/stop)·
실시간 스트림(SSE/WS)은 데몬 통합 단계에서 추가한다(P1 범위는 읽기 + 인증 + 마스킹).

실행: uvicorn src.api.app:app  또는  create_app() (테스트). 보호 엔드포인트는 X-API-Key 필요.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, Query

from src.api import service
from src.api.auth import require_api_key
from src.utils.config import AppConfig
from src.utils.health import (
    HealthChecker,
    check_disk_space,
    check_memory_usage,
    register_pipeline_health_checks,
)


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

    return app


app = create_app()
