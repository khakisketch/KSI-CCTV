"""P3-T2: 기간 집계 + 보고서 저장소 테스트 (TDD RED).

축소 규칙: 상한 초과 시 버킷 요약 우선, 원문(개별 이벤트) 절단 금지(통째로만 제외).
저장소: Report CRUD + 보존 기본 90일(config 키 `reports.retention_days` 정의만).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# fakes (저장소 SSOT 시그니처 준수: events.list / observations.query / incidents.list)
# ---------------------------------------------------------------------------


class _FakeEvents:
    def __init__(self, items: list[dict]) -> None:
        self._items = items

    def list(self, **kwargs: object) -> list[dict]:
        return list(self._items)


class _FakeObs:
    def __init__(self, buckets: list[dict]) -> None:
        self._buckets = buckets
        self.calls: list[int] = []

    def query(self, camera_id: str | None = None, limit: int = 120) -> list[dict]:
        self.calls.append(int(limit))
        rows = [b for b in self._buckets if camera_id is None or b["camera_id"] == camera_id]
        return rows[: max(1, int(limit))]


class _FakeIncidents:
    def __init__(self, items: list[dict]) -> None:
        self._items = items
        self.calls: list[int] = []

    def list(self, **kwargs: object) -> list[dict]:
        limit = int(kwargs.get("limit", 200))
        self.calls.append(limit)
        return list(self._items)[: max(1, limit)]


def _event(event_id: str, marker: str = "", timestamp: str = "2026-09-21T10:00:00+00:00") -> dict:
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "camera_id": "cam-1",
        "event_type": "no_helmet",
        "alert_level": "warning",
        "note": marker,
    }


def _bucket(camera_id: str, start: str, samples: int = 3) -> dict:
    return {
        "camera_id": camera_id,
        "bucket_start": start,
        "samples": samples,
        "latest_counts": {"person": 1},
        "max_counts": {"person": 2},
        "model_counts": {"yolo": 2},
        "zone_max_counts": {},
    }


def _incident(event_id: str, updated_at: str = "2026-09-21T10:00:00+00:00") -> dict:
    return {
        "event_id": event_id,
        "category": "safety",
        "camera_id": "cam-1",
        "status": "new",
        "updated_at": updated_at,
    }


def _aggregator(events=None, buckets=None, incidents=None):  # type: ignore[no-untyped-def]
    from src.reports.aggregator import PeriodAggregator

    return PeriodAggregator(
        event_repo=_FakeEvents(events or []),
        observation_store=_FakeObs(buckets or []),
        incident_store=_FakeIncidents(incidents or []),
    )


# ---------------------------------------------------------------------------
# aggregator: build_context(since, until, max_tokens)
# ---------------------------------------------------------------------------


def test_build_context_within_budget_includes_all() -> None:
    from src.reports.aggregator import estimate_tokens

    agg = _aggregator(
        events=[_event("ev-1"), _event("ev-2")],
        buckets=[_bucket("cam-1", "2026-09-21T10:00:00+00:00")],
        incidents=[_incident("ev-1")],
    )
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 4000)
    assert "ev-1" in ctx and "ev-2" in ctx
    assert estimate_tokens(ctx) <= 4000


def test_build_context_over_budget_prefers_bucket_summary() -> None:
    from src.reports.aggregator import estimate_tokens

    buckets = [_bucket("cam-1", f"2026-09-21T10:{m:02d}:00+00:00") for m in range(30)]
    events = [_event("ev-1"), _event("ev-2")]
    agg = _aggregator(events=events, buckets=buckets, incidents=[])
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 300)
    # 버킷은 요약 형태로 축소되고, 원문 이벤트는 보존된다.
    assert "- buckets " in ctx
    assert "- bucket " not in ctx
    assert "ev-1" in ctx and "ev-2" in ctx
    assert estimate_tokens(ctx) <= 300


def test_build_context_never_truncates_raw_event() -> None:
    from src.reports.aggregator import estimate_tokens

    markers = [f"MARKER-{i:03d}-" + "x" * 120 for i in range(6)]
    events = [_event(f"ev-{i}", marker=markers[i]) for i in range(6)]
    agg = _aggregator(events=events, buckets=[], incidents=[])
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 200)
    assert estimate_tokens(ctx) <= 200
    for marker in markers:
        if marker in ctx:
            continue
        # 제외된 원문은 일부라도 남지 않는다 (앞 50자 조각 검사).
        assert marker[:50] not in ctx


def test_build_context_drops_oldest_first() -> None:
    events = [_event(f"ev-new-{i}") for i in range(2)] + [_event(f"ev-old-{i}") for i in range(6)]
    agg = _aggregator(events=events, buckets=[], incidents=[])
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 150)
    assert "ev-new-0" in ctx
    assert "ev-old-5" not in ctx


def test_estimate_tokens_basis() -> None:
    from src.reports.aggregator import estimate_tokens

    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 8) == 2


# ---------------------------------------------------------------------------
# store: ReportStore.save/get/list/prune
# ---------------------------------------------------------------------------


def test_report_record_schema_and_roundtrip(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.reports.store import ReportStore

    store = ReportStore(tmp_path / "reports.db")
    try:
        report_id = store.save(
            {
                "period": "daily",
                "since": "2026-09-21T00:00:00+00:00",
                "until": "2026-09-22T00:00:00+00:00",
                "model": "llama3.1",
                "markdown": "# 일일 보고\n- 이상 없음",
            }
        )
        assert isinstance(report_id, int)
        got = store.get(report_id)
        assert got is not None
        assert got["id"] == report_id
        assert got["period"] == "daily"
        assert got["since"] == "2026-09-21T00:00:00+00:00"
        assert got["until"] == "2026-09-22T00:00:00+00:00"
        assert got["model"] == "llama3.1"
        assert got["markdown"] == "# 일일 보고\n- 이상 없음"
        assert got["created_at"]
    finally:
        store.close()


def test_report_store_save_rejects_bad_period(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.reports.store import ReportStore

    store = ReportStore(tmp_path / "reports.db")
    try:
        import pytest

        with pytest.raises(ValueError):
            store.save(
                {
                    "period": "hourly",
                    "since": "2026-09-21T00:00:00+00:00",
                    "until": "2026-09-22T00:00:00+00:00",
                    "model": "llama3.1",
                    "markdown": "x",
                }
            )
    finally:
        store.close()


def test_report_store_get_missing_returns_none(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.reports.store import ReportStore

    store = ReportStore(tmp_path / "reports.db")
    try:
        assert store.get(9999) is None
    finally:
        store.close()


def test_report_store_list_newest_first(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.reports.store import ReportStore

    store = ReportStore(tmp_path / "reports.db")
    try:
        first = store.save(
            {
                "period": "daily",
                "since": "2026-09-20T00:00:00+00:00",
                "until": "2026-09-21T00:00:00+00:00",
                "model": "m",
                "markdown": "first",
            }
        )
        second = store.save(
            {
                "period": "weekly",
                "since": "2026-09-14T00:00:00+00:00",
                "until": "2026-09-21T00:00:00+00:00",
                "model": "m",
                "markdown": "second",
            }
        )
        rows = store.list(limit=10)
        assert [r["id"] for r in rows] == [second, first]
    finally:
        store.close()


def _backdate_created_at(path, report_id: int, created_at: str) -> None:  # type: ignore[no-untyped-def]
    conn = sqlite3.connect(path)
    try:
        conn.execute("UPDATE reports SET created_at = ? WHERE id = ?", (created_at, report_id))
        conn.commit()
    finally:
        conn.close()


def test_report_store_prune_default_90days(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.reports.store import DEFAULT_RETENTION_DAYS, RETENTION_CONFIG_KEY, ReportStore

    assert DEFAULT_RETENTION_DAYS == 90
    assert RETENTION_CONFIG_KEY == "reports.retention_days"
    store = ReportStore(tmp_path / "reports.db")
    try:
        old_id = store.save(
            {
                "period": "daily",
                "since": "2026-06-01T00:00:00+00:00",
                "until": "2026-06-02T00:00:00+00:00",
                "model": "m",
                "markdown": "old",
            }
        )
        fresh_id = store.save(
            {
                "period": "daily",
                "since": "2026-09-21T00:00:00+00:00",
                "until": "2026-09-22T00:00:00+00:00",
                "model": "m",
                "markdown": "fresh",
            }
        )
        now = _utcnow()
        _backdate_created_at(
            tmp_path / "reports.db",
            old_id,
            (datetime.now(timezone.utc) - timedelta(days=91)).isoformat(),
        )
        deleted = store.prune(now=datetime.fromisoformat(now))
        assert deleted == 1
        assert store.get(old_id) is None
        assert store.get(fresh_id) is not None
    finally:
        store.close()


def test_report_store_prune_custom_retention(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from src.reports.store import ReportStore

    store = ReportStore(tmp_path / "reports.db")
    try:
        report_id = store.save(
            {
                "period": "daily",
                "since": "2026-09-21T00:00:00+00:00",
                "until": "2026-09-22T00:00:00+00:00",
                "model": "m",
                "markdown": "x",
            }
        )
        _backdate_created_at(
            tmp_path / "reports.db",
            report_id,
            (datetime.now(timezone.utc) - timedelta(days=10)).isoformat(),
        )
        assert store.prune(retention_days=7) == 1
        assert store.get(report_id) is None
    finally:
        store.close()


# ---------------------------------------------------------------------------
# R3 fix round: I-1/I-2/I-3 + M-1/M-2/M-3 회귀 테스트
# ---------------------------------------------------------------------------


def _seed_old_edge_fresh(store, base: datetime) -> tuple[int, int, int]:  # type: ignore[no-untyped-def]
    """고정 기준시각 대비 old(91일 전)·edge(정확히 90일 전)·fresh(1일 전) 3건을 심는다."""

    def _save(since: str) -> int:
        return store.save(
            {
                "period": "daily",
                "since": since,
                "until": since,
                "model": "m",
                "markdown": "x",
            }
        )

    old_id = _save("2026-06-01T00:00:00+00:00")
    edge_id = _save("2026-06-23T00:00:00+00:00")
    fresh_id = _save("2026-09-21T00:00:00+00:00")
    _backdate_created_at(store.path, old_id, (base - timedelta(days=91)).isoformat())
    _backdate_created_at(store.path, edge_id, (base - timedelta(days=90)).isoformat())
    _backdate_created_at(store.path, fresh_id, (base - timedelta(days=1)).isoformat())
    return old_id, edge_id, fresh_id


def test_prune_naive_now_treated_as_utc(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """I-1: naive now는 UTC로 간주한다 — aware 전달과 동일한 삭제 결과 + 경계 유지.

    같은 순간의 non-UTC aware now도 동일해야 한다 (문자열 비교 파괴 회귀).
    """
    from src.reports.store import ReportStore

    base = datetime(2026, 9, 22, 12, 0, 0, tzinfo=timezone.utc)
    kst = timezone(timedelta(hours=9))
    cases = {
        "naive.db": base.replace(tzinfo=None),
        "aware.db": base,
        "offset.db": base.astimezone(kst),  # 같은 순간, wall clock 표기만 다름
    }
    stores = {}
    try:
        for name, now in cases.items():
            store = ReportStore(tmp_path / name)
            stores[name] = store
            old_id, edge_id, fresh_id = _seed_old_edge_fresh(store, base)
            assert store.prune(now=now) == 1, name
            assert store.get(old_id) is None, name
            # 경계(정확히 cutoff)는 `<` 비교이므로 유지된다.
            assert store.get(edge_id) is not None, name
            assert store.get(fresh_id) is not None, name
    finally:
        for store in stores.values():
            store.close()


def test_build_context_late_stage_order_incidents_before_buckets() -> None:
    """I-2: 후반 축소 순서 고정 — 이벤트 전멸 후 인시던트가 버킷 요약보다 먼저 탈락한다."""
    from src.reports.aggregator import estimate_tokens

    events = [_event("ev-1"), _event("ev-2")]
    incidents = [_incident(f"in-{i:02d}-" + "y" * 100) for i in range(8)]
    buckets = [
        _bucket("cam-1", "2026-09-21T10:00:00+00:00"),
        _bucket("cam-1", "2026-09-21T10:01:00+00:00"),
    ]
    agg = _aggregator(events=events, buckets=buckets, incidents=incidents)
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 250)
    assert estimate_tokens(ctx) <= 250
    # 이벤트는 모두 탈락하고, 인시던트 일부가 탈락하지만 버킷 요약은 남는다.
    assert "- events omitted: 2" in ctx
    assert "- incidents omitted:" in ctx
    assert "- buckets " in ctx
    assert "- bucket " not in ctx


def test_paged_fetch_recovers_window_buckets_beyond_first_page() -> None:
    """I-3: 윈도우 밖 대량 행이 DB LIMIT를 선점해도 윈도우 행을 회수한다 (버킷)."""
    from src.reports.aggregator import estimate_tokens

    since = "2026-09-21T00:00:00+00:00"
    until = "2026-09-21T06:00:00+00:00"
    base = datetime(2026, 9, 21, 7, 0, 0, tzinfo=timezone.utc)
    out_starts = [(base + timedelta(minutes=i)).isoformat() for i in range(2500)]
    outs = [_bucket("cam-out", start) for start in reversed(out_starts)]  # newest-first
    ins = [
        _bucket("cam-in", "2026-09-21T01:00:00+00:00"),
        _bucket("cam-in", "2026-09-21T02:00:00+00:00"),
        _bucket("cam-in", "2026-09-21T03:00:00+00:00"),
    ]
    fake = _FakeObs(outs + ins)
    agg = _aggregator(events=[], buckets=[], incidents=[])
    agg.observation_store = fake
    ctx = agg.build_context(since, until, 4000)
    assert estimate_tokens(ctx) <= 4000
    assert max(fake.calls) > 500  # 첫 페이지를 넘어 순회했음
    # 첫 페이지(500행)에 가려졌던 윈도우 행이 회수되었다.
    assert "- bucket cam-in 2026-09-21T01:00:00+00:00" in ctx
    assert "- bucket cam-in 2026-09-21T02:00:00+00:00" in ctx
    assert "- bucket cam-in 2026-09-21T03:00:00+00:00" in ctx


def test_paged_fetch_recovers_window_incidents_beyond_first_page() -> None:
    """I-3: 윈도우 밖 대량 행이 DB LIMIT를 선점해도 윈도우 행을 회수한다 (인시던트)."""
    from src.reports.aggregator import estimate_tokens

    since = "2026-09-21T00:00:00+00:00"
    until = "2026-09-21T06:00:00+00:00"
    base = datetime(2026, 9, 21, 7, 0, 0, tzinfo=timezone.utc)
    outs = [
        _incident(f"out-{i:04d}", (base + timedelta(minutes=i)).isoformat()) for i in range(1200)
    ]
    outs.reverse()  # newest-first
    ins = [
        _incident("in-a", "2026-09-21T01:00:00+00:00"),
        _incident("in-b", "2026-09-21T02:00:00+00:00"),
    ]
    fake = _FakeIncidents(outs + ins)
    agg = _aggregator(events=[], buckets=[], incidents=[])
    agg.incident_store = fake
    ctx = agg.build_context(since, until, 4000)
    assert estimate_tokens(ctx) <= 4000
    assert max(fake.calls) > 500
    assert "in-a" in ctx and "in-b" in ctx


def test_build_context_best_effort_below_header_budget() -> None:
    """M-1: 상한이 헤더보다 작아도 헤더를 반환한다 (best-effort, 원문 아님)."""
    agg = _aggregator(events=[_event("ev-1")], buckets=[], incidents=[])
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 1)
    assert ctx.startswith("# report-context\n")


def test_build_context_reports_omitted_bucket_count() -> None:
    """M-2: 버킷 탈락 시 제외된 원본 버킷 행 수를 표기한다."""
    from src.reports.aggregator import estimate_tokens

    buckets = [
        _bucket(f"cam-{c}", f"2026-09-21T10:{c * 3 + m:02d}:00+00:00")
        for c in range(10)
        for m in range(3)
    ]
    agg = _aggregator(events=[], buckets=buckets, incidents=[])
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 60)
    assert estimate_tokens(ctx) <= 60
    assert "- buckets omitted: 30" in ctx


def test_build_context_defensive_sort_drops_chronological_oldest() -> None:
    """M-3: 저장소 순서와 무관하게 시간상 가장 오래된 것부터 탈락한다."""
    events = [_event(f"ev-{i}", timestamp=f"2026-09-21T10:{i:02d}:00+00:00") for i in range(8)]
    agg = _aggregator(events=events, buckets=[], incidents=[])
    ctx = agg.build_context("2026-09-21T00:00:00+00:00", "2026-09-22T00:00:00+00:00", 150)
    assert "ev-7" in ctx
    assert "ev-0" not in ctx
