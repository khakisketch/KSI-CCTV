"""헬스/제어 라우트. 경로·태그·상태코드·의존성·응답 형태·등록 순서 불변 이동."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from src.api import service


def register(app, auth, *, build_checker: Callable[[], Any]) -> None:
    # ── 헬스(/api 인증 뷰) ───────────────────────────────────────────────
    @app.get("/api/health/live", tags=["health"])
    def health_live() -> dict[str, str]:
        """liveness 프로브 — 프로세스 생존 시 항상 200(비인증)."""
        return {"status": "alive"}

    @app.get("/api/health", tags=["health"], dependencies=auth)
    def health() -> dict[str, Any]:
        return build_checker().run_checks().to_dict()

    # ── 헬스(compat: 기존 http.server /health* 대체 — 비인증, 컨테이너/systemd 프로브용) ──
    @app.get("/health/live", tags=["health"], include_in_schema=False)
    @app.get("/healthz", tags=["health"], include_in_schema=False)
    def compat_live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health", tags=["health"], include_in_schema=False)
    def compat_health() -> JSONResponse:
        report = build_checker().run_checks()
        # 비인증 compatibility probe는 liveness/readiness 용도만 제공한다. component
        # messages, disk/GPU metrics, camera/model metadata는 인증된 /api/health에서만 조회.
        return JSONResponse(
            {"status": "healthy" if report.overall_healthy else "unhealthy"},
            status_code=200 if report.overall_healthy else 503,
        )

    @app.get("/health/ready", tags=["health"], include_in_schema=False)
    def compat_ready() -> JSONResponse:
        report = build_checker().run_checks()
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
