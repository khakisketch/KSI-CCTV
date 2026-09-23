"""정기 보고서 조회 API (P3-T4, 읽기 전용 3종).

GET /api/reports (목록+스케줄 상태) · GET /api/reports/{id} (열람) ·
GET /api/reports/{id}/download (Markdown 첨부). 쓰기 API는 없다.

저장소 해결: 테스트·데몬이 ``app.state.report_store``를 주입하면 그대로 쓰고,
없으면 ``app.state.config``의 ``reports.db_path``(T3 키) 파일이 있을 때만 열어
``app.state``에 캐시한다 — T3 스케줄러와 같은 DB 파일을 읽는다. 파일 부재 시
읽기 경로는 파일을 생성하지 않고 빈 목록·404를 반환한다.
스케줄 상태: ``app.state.report_scheduler``가 있으면 live ``status()``를,
없으면 저장소 최신본+config 주기로 유도한다(``live:false``, failure/last_run은
인메모리에만 있어 null). 조회는 감사 로그를 남기지 않는다(기존 GET 관행).
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query
from fastapi.responses import PlainTextResponse

from src.reports.scheduler import PERIOD_INTERVALS
from src.reports.store import DEFAULT_RETENTION_DAYS, ReportStore

logger = logging.getLogger("ksi_cctv.api.reports")

_DEFAULT_DB_PATH = "./data/reports.db"
_LIST_MAX_LIMIT = 200

_META_KEYS = ("id", "period", "since", "until", "model", "created_at")

# M-2: 지연 생성 경쟁 방지 (주입 경로는 락 불필요 — 생성 분기만 보호).
_STORE_LOCK = threading.Lock()


def _config_section(config: Any) -> dict[str, Any]:
    """reports 섹션을 dict로 읽는다 (AppConfig.get·plain dict 모두 수용)."""
    if config is None:
        return {}
    if isinstance(config, dict):
        section = config.get("reports", {})
        return dict(section) if isinstance(section, dict) else {}
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            section = getter("reports", default={})
        except TypeError:
            return {}
        return dict(section) if isinstance(section, dict) else {}
    return {}


def _store_for(app: Any) -> ReportStore | None:
    """주입된 스케줄러 저장소를 우선 사용한다.

    데몬(run.py)은 스케줄러의 실 저장소를 주입한다. 미주입 시에는 config 경로의
    DB 파일이 이미 있을 때만 열어 캐시하고, 파일 부재 시에는 생성하지 않고
    None을 반환한다 (R8 I-2: 읽기 GET은 파일을 만들지 않는다 — 빈 목록·404).
    """
    injected = getattr(app.state, "report_store", None)
    if injected is not None:
        return injected
    with _STORE_LOCK:
        injected = getattr(app.state, "report_store", None)
        if injected is not None:
            return injected
        section = _config_section(getattr(app.state, "config", None))
        db_path = str(section.get("db_path") or _DEFAULT_DB_PATH)
        if not Path(db_path).exists():
            return None
        store = ReportStore(db_path)
        app.state.report_store = store
        return store


def _retention_for(section: dict[str, Any]) -> int:
    """표시용 보존기간 — 비정상(문자열·0 이하)은 스케줄러와 같은 기본 90일로 clamp (M-1)."""
    try:
        retention = int(section.get("retention_days", DEFAULT_RETENTION_DAYS))
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    if retention < 1:
        return DEFAULT_RETENTION_DAYS
    return retention


def _live_periods(section: dict[str, Any], live: dict[str, Any]) -> dict[str, Any]:
    """live 상태를 양 주기로 정규화 — 꺼진 주기는 enabled:false+null 보완 (I-1)."""
    periods = {}
    for period in PERIOD_INTERVALS:
        state = live.get(period)
        if not isinstance(state, dict):
            state = {}
        periods[period] = {
            "enabled": bool(section.get(f"{period}_enabled", True)),
            "last_run": state.get("last_run"),
            "last_success": state.get("last_success"),
            "next_run": state.get("next_run"),
            "failure_count": state.get("failure_count"),
        }
    return periods


def _latest_per_period(store: ReportStore | None) -> dict[str, str]:
    latest: dict[str, str] = {}
    if store is None:
        return latest
    for row in store.list(limit=_LIST_MAX_LIMIT):
        period = str(row.get("period", ""))
        if period and period not in latest:
            latest[period] = str(row.get("created_at", ""))
    return latest


def _status_for(app: Any, store: ReportStore | None) -> dict[str, Any]:
    section = _config_section(getattr(app.state, "config", None))
    enabled = bool(section.get("enabled", False))
    retention = _retention_for(section)
    scheduler = getattr(app.state, "report_scheduler", None)
    if scheduler is not None:
        try:
            live = scheduler.status()
        except Exception as exc:
            # I-4: live 조회 실패는 목록 전체 500이 아니라 유도값 fallback으로 격리.
            logger.warning("report scheduler status failed, using derived fallback: %s", exc)
            live = None
        if isinstance(live, dict):
            return {
                "enabled": enabled,
                "live": True,
                "retention_days": retention,
                "periods": _live_periods(section, live),
            }
    latest = _latest_per_period(store)
    periods = {}
    for period, interval in PERIOD_INTERVALS.items():
        last_success = latest.get(period)
        next_run: str | None = None
        if last_success:
            try:
                point = datetime.fromisoformat(last_success)
                if point.tzinfo is None:
                    point = point.replace(tzinfo=timezone.utc)
                next_run = (point + interval).isoformat()
            except ValueError:
                next_run = None
        periods[period] = {
            "enabled": bool(section.get(f"{period}_enabled", True)),
            "last_run": None,
            "last_success": last_success,
            "next_run": next_run,
            "failure_count": None,
        }
    return {
        "enabled": enabled,
        "live": False,
        "retention_days": retention,
        "periods": periods,
    }


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in _META_KEYS}


def register(app, auth) -> None:
    @app.get("/api/reports", tags=["reports"], dependencies=auth)
    def reports_list(
        limit: int = Query(100, ge=1, le=_LIST_MAX_LIMIT),
        period: str | None = Query(None, max_length=16),
    ) -> dict[str, Any]:
        if period is not None and period not in PERIOD_INTERVALS:
            raise HTTPException(status_code=400, detail="period는 daily/weekly 중 하나여야 합니다.")
        store = _store_for(app)
        if store is None:
            # R8 I-2: DB 파일 부재 — 생성 없이 빈 목록 + 유도 상태.
            return {"count": 0, "reports": [], "status": _status_for(app, None)}
        # I-2: period 필터는 SQL WHERE로 절단 전에 적용한다 (limit 절단 뒤 필터 금지).
        rows = store.list(limit=limit, period=period)
        return {
            "count": len(rows),
            "reports": [_meta(row) for row in rows],
            "status": _status_for(app, store),
        }

    @app.get("/api/reports/{report_id}", tags=["reports"], dependencies=auth)
    def report_detail(report_id: int) -> dict[str, Any]:
        store = _store_for(app)
        report = store.get(report_id) if store is not None else None
        if report is None:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
        return {"report": report}

    @app.get("/api/reports/{report_id}/download", tags=["reports"], dependencies=auth)
    def report_download(report_id: int) -> PlainTextResponse:
        store = _store_for(app)
        report = store.get(report_id) if store is not None else None
        if report is None:
            raise HTTPException(status_code=404, detail="보고서를 찾을 수 없습니다.")
        return PlainTextResponse(
            str(report.get("markdown", "")),
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="report-{report_id}.md"',
            },
        )
