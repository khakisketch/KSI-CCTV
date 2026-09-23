"""P3-T3: 정기 보고서 스케줄러 + 데몬 배선.

주기 tick → 집계(aggregator) → 생성(provider) → 저장(store).
실패(LLM 예외)는 건너뛰고 다음 주기로 이월한다 — 빈 보고서·플레이스홀더
저장 금지. 상태(last_run/last_success/next_run/failure_count)를 노출한다.

timeout clamp (T1 리뷰 M-1 이관): config ``timeout_sec`` 비정상은
``requests`` 무제한 대기(hang)로 이어지므로 기본값으로 되돌린다.
"""

from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from src.reports.aggregator import PeriodAggregator
from src.reports.provider import OllamaTextProvider, ReportProvider
from src.reports.store import DEFAULT_RETENTION_DAYS, ReportStore

logger = logging.getLogger("ksi_cctv.reports.scheduler")

DEFAULT_TIMEOUT_SEC = 60.0
MIN_TIMEOUT_SEC = 1.0
MAX_TIMEOUT_SEC = 300.0
DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_DB_PATH = "./data/reports.db"
DEFAULT_MAX_CONTEXT = 8000
_STOP_JOIN_TIMEOUT_SEC = 5.0
_TICK_CHECK_INTERVAL_SEC = 60.0

PERIOD_INTERVALS: dict[str, timedelta] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(weeks=1),
}


def clamp_timeout_sec(value: Any) -> float:
    """config timeout을 hang 없는 범위로 되돌린다.

    비정상(None·비수치·NaN/inf·0 이하·상한 초과)은 기본값/상한으로 clamp한다.
    ``requests`` timeout에 0·None이 들어가면 무제한 대기가 되므로,
    0 이하는 최소값이 아니라 기본값으로 되돌린다.
    """
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SEC
    if not math.isfinite(seconds) or seconds <= 0:
        return DEFAULT_TIMEOUT_SEC
    if seconds < MIN_TIMEOUT_SEC:
        return MIN_TIMEOUT_SEC
    if seconds > MAX_TIMEOUT_SEC:
        return MAX_TIMEOUT_SEC
    return seconds


def _coerce_now(now: datetime | None) -> datetime:
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


class ReportScheduler:
    """일/주 주기 보고서 스케줄러 (스레드 안전)."""

    def __init__(
        self,
        provider: ReportProvider,
        aggregator: PeriodAggregator,
        store: ReportStore,
        periods: tuple[str, ...] = ("daily", "weekly"),
        max_context: int = 8000,
        retention_days: int = 90,
        now_fn: Any | None = None,
    ) -> None:
        unknown = [period for period in periods if period not in PERIOD_INTERVALS]
        if unknown:
            raise ValueError(f"unknown report period: {unknown}")
        self.provider = provider
        self.aggregator = aggregator
        self.store = store
        self.periods = tuple(periods)
        self.max_context = max(1, int(max_context))
        self.retention_days = retention_days
        self._now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self._state: dict[str, dict[str, Any]] = {
            period: {
                "last_run": None,
                "last_success": None,
                "next_run": None,  # None = 즉시 발화 (최초 tick)
                "failure_count": 0,
                "window_start": None,  # 미보고 구간의 시작 (성공 시에만 전진)
            }
            for period in self.periods
        }
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def tick(self, now: datetime | None = None) -> list[dict[str, Any]]:
        """발화 시점이 된 주기를 실행하고 이번 tick에서 저장된 레코드를 반환한다."""
        current = _coerce_now(now) if now is not None else _coerce_now(self._now_fn())
        saved: list[dict[str, Any]] = []
        for period in self.periods:
            record = self._tick_period(period, current)
            if record is not None:
                saved.append(record)
        return saved

    def status(self) -> dict[str, dict[str, Any]]:
        """주기별 상태(last_run/last_success/next_run/failure_count)."""
        with self._lock:
            return {
                period: {
                    "last_run": state["last_run"],
                    "last_success": state["last_success"],
                    "next_run": state["next_run"],
                    "failure_count": state["failure_count"],
                }
                for period, state in self._state.items()
            }

    def start(self) -> None:
        """백그라운드 발화 스레드를 기동한다 (daemon — 프로세스 종료를 막지 않음)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._serve, daemon=True, name="report-scheduler"
            )
            self._thread.start()

    def stop(self) -> None:
        """발화 스레드 정지를 요청하고 join한다 (best-effort).

        join 타임아웃 안에 스레드가 끝나지 않으면 경고를 남기고 반환한다 —
        호출 후 스레드가 반드시 죽었다고 보장하지 않는다 (daemon 스레드라
        프로세스 종료를 막지는 않는다).
        """
        with self._lock:
            thread = self._thread
        self._stop.set()
        if thread is not None:
            thread.join(timeout=_STOP_JOIN_TIMEOUT_SEC)
            if thread.is_alive():
                logger.warning(
                    "report scheduler thread did not stop within %.1fs",
                    _STOP_JOIN_TIMEOUT_SEC,
                )

    def close(self) -> None:
        self.stop()
        close = getattr(self.store, "close", None)
        if callable(close):
            close()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # pragma: no cover - 방어적 (tick 내부는 건별 처리)
                logger.exception("report scheduler tick failed")
            self._stop.wait(_TICK_CHECK_INTERVAL_SEC)

    def _tick_period(self, period: str, now: datetime) -> dict[str, Any] | None:
        with self._lock:
            state = self._state[period]
            next_run = state["next_run"]
            if next_run is not None and now < datetime.fromisoformat(next_run):
                return None
            if state["window_start"] is None:
                # 시도 윈도우를 고정한다 — 실패해도 유지되어 다음 주기로 이월된다.
                state["window_start"] = (now - PERIOD_INTERVALS[period]).isoformat()
            window_start = state["window_start"]
            state["last_run"] = now.isoformat()
        interval = PERIOD_INTERVALS[period]
        try:
            context = self.aggregator.build_context(window_start, now.isoformat(), self.max_context)
            markdown = self.provider.generate(f"# periodic-report ({period})\n{context}\n")
            record = {
                "period": period,
                "since": window_start,
                "until": now.isoformat(),
                "model": str(getattr(self.provider, "model", "")),
                "markdown": markdown,
            }
            report_id = self.store.save(record)
            pruned = self.store.prune(self.retention_days, now=now)
            if pruned:
                logger.info("expired reports pruned: %d", pruned)
        except Exception as exc:
            # 실패는 건너뛰고 다음 주기로 이월한다 (window_start 유지).
            with self._lock:
                state["failure_count"] += 1
                state["next_run"] = (now + interval).isoformat()
            logger.warning("report generation skipped (%s): %s", period, exc, exc_info=True)
            return None
        with self._lock:
            state["last_success"] = now.isoformat()
            state["next_run"] = (now + interval).isoformat()
            state["failure_count"] = 0
            state["window_start"] = now.isoformat()
        saved = dict(record)
        saved["id"] = report_id
        return saved


def _nested_get(config: Any, *keys: str, default: Any = None) -> Any:
    """AppConfig(가변 .get) 또는 plain dict 모두에서 중첩값을 읽는다."""
    if isinstance(config, dict):
        value: Any = config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value
    getter = getattr(config, "get", None)
    if callable(getter):
        try:
            return getter(*keys, default=default)
        except TypeError:
            return default
    return default


def build_report_scheduler(
    config: Any,
    event_repository: Any | None = None,
    observation_store: Any | None = None,
    incident_store: Any | None = None,
    report_store: ReportStore | None = None,
    report_store_path: str | None = None,
    now_fn: Any | None = None,
) -> ReportScheduler | None:
    """config에서 스케줄러를 조립한다. 비활성(reports.enabled:false)이면 None.

    데몬 경로는 실저장소 객체를 그대로 전달한다 — None·스텁 치환 금지.
    활성화 상태에서 모델 미지정·비로컬 endpoint는 ValueError (fail-closed).
    """
    reports_cfg = _nested_get(config, "reports", default={}) or {}
    if not isinstance(reports_cfg, dict) or not reports_cfg.get("enabled", False):
        return None
    # M-2: periods 전부 off면 inert(None) — 모델 검증보다 먼저 판단한다.
    enabled_periods = tuple(
        period
        for period in PERIOD_INTERVALS
        if reports_cfg.get(f"{period}_enabled", True)
    )
    if not enabled_periods:
        return None
    llm_cfg = _nested_get(config, "models", "report_llm", default={}) or {}
    if not isinstance(llm_cfg, dict):
        llm_cfg = {}
    model = str(llm_cfg.get("model_name", "") or "")
    provider = OllamaTextProvider(
        base_url=str(llm_cfg.get("base_url") or DEFAULT_BASE_URL),
        model=model,
        timeout_sec=clamp_timeout_sec(llm_cfg.get("timeout_sec", DEFAULT_TIMEOUT_SEC)),
    )
    store = report_store or ReportStore(
        report_store_path
        or str(reports_cfg.get("db_path", DEFAULT_DB_PATH))
    )
    aggregator = PeriodAggregator(
        event_repo=event_repository,
        observation_store=observation_store,
        incident_store=incident_store,
    )
    max_context = reports_cfg.get("max_context", DEFAULT_MAX_CONTEXT)
    try:
        max_context = int(max_context)
    except (TypeError, ValueError):
        max_context = DEFAULT_MAX_CONTEXT
    if max_context < 1:
        # M-3: 비정상 토큰 상한은 기본값으로 — 1토큰 강행 금지.
        max_context = DEFAULT_MAX_CONTEXT
    retention = reports_cfg.get("retention_days", DEFAULT_RETENTION_DAYS)
    try:
        retention = int(retention)
    except (TypeError, ValueError):
        retention = DEFAULT_RETENTION_DAYS
    if retention < 1:
        # I-2: 비정상 보존기간은 기본 90일 — 1일 과삭제(파괴적) 금지.
        retention = DEFAULT_RETENTION_DAYS
    return ReportScheduler(
        provider=provider,
        aggregator=aggregator,
        store=store,
        periods=enabled_periods,
        max_context=max_context,
        retention_days=retention,
        now_fn=now_fn or (lambda: datetime.now(timezone.utc)),
    )
