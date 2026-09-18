"""API 앱 주입 헬퍼 — 라우트 모듈에 전달되는 zero-arg callable의 실체.

app.py의 내부 클로저(`_build_checker`/`_runtime_event_catalog`)를 동작 불변으로
이동한 것이다. 라우트 모듈은 시그니처 변경 없이 `functools.partial`로 바인딩된
callable을 받는다.
"""
from __future__ import annotations

from typing import Any

from src.api import service
from src.utils.health import (
    HealthChecker,
    check_disk_space,
    check_memory_usage,
    register_pipeline_health_checks,
)


def build_health_checker(app: Any, checker_factory: Any | None = None) -> HealthChecker:
    """헬스 체커 생성. checker_factory 우선, 기본 disk/memory 등록."""
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


def runtime_event_catalog(app: Any) -> dict[str, Any]:
    """런타임 AI 가용성을 반영한 이벤트 카탈로그."""
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
