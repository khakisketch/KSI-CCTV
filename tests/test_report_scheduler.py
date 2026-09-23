"""P3-T3: 스케줄러 + 데몬 배선 테스트 (TDD RED).

주기 tick → 집계 → 생성 → 저장, 실패 시 다음 주기 이월 + 상태 노출.
기본 비활성·timeout clamp·base_url 기본값·데몬 경로 기동 회귀를 고정한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml


def _utc(year: int = 2026, month: int = 9, day: int = 22) -> datetime:
    return datetime(year, month, day, tzinfo=timezone.utc)


class _FakeProvider:
    """실 LLM 대신 주입하는 생성기 (실패 주입 가능)."""

    def __init__(self, model: str = "llama3.1", text: str = "# daily report") -> None:
        self.model = model
        self.text = text
        self.calls: list[str] = []
        self.failures: int = 0

    def generate(self, prompt: str) -> str:
        self.calls.append(prompt)
        if self.failures > 0:
            self.failures -= 1
            raise RuntimeError("LLM boom")
        return self.text


class _FakeAggregator:
    """실 저장소 대신 주입하는 집계기 (호출 윈도우 기록)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, int]] = []

    def build_context(self, since: str, until: str, max_context: int = 8000) -> str:
        self.calls.append((since, until, max_context))
        return f"context {since}..{until}"


def _scheduler(tmp_path: Path, provider=None, aggregator=None, **kwargs):
    from src.reports.scheduler import ReportScheduler
    from src.reports.store import ReportStore

    store = ReportStore(tmp_path / "reports.db")
    return ReportScheduler(
        provider=provider or _FakeProvider(),
        aggregator=aggregator or _FakeAggregator(),
        store=store,
        **kwargs,
    )


# ── 주기 발화: daily/weekly tick → 집계 → 생성 → 저장 ──


def test_daily_tick_generates_and_stores(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path, periods=("daily",))
    now = _utc()
    saved = sched.tick(now)

    assert len(saved) == 1
    assert saved[0]["period"] == "daily"
    assert saved[0]["markdown"] == "# daily report"
    assert sched.store.list()[0]["period"] == "daily"


def test_tick_skips_when_not_due(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path, periods=("daily",))
    now = _utc()
    assert len(sched.tick(now)) == 1
    # daily 간격(24h) 전에는 발화하지 않는다.
    assert sched.tick(now + timedelta(hours=1)) == []
    assert len(sched.store.list()) == 1
    # 간격 경과 후 다시 발화한다 (이번 tick 저장 1건, 누적 2건).
    assert len(sched.tick(now + timedelta(days=1, seconds=1))) == 1
    assert len(sched.store.list()) == 2


def test_weekly_interval_is_seven_days(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path, periods=("weekly",))
    now = _utc()
    assert len(sched.tick(now)) == 1
    assert sched.tick(now + timedelta(days=6)) == []
    assert len(sched.tick(now + timedelta(days=7, seconds=1))) == 1
    assert len(sched.store.list()) == 2


def test_tick_passes_window_to_aggregator(tmp_path: Path) -> None:
    agg = _FakeAggregator()
    sched = _scheduler(tmp_path, aggregator=agg, periods=("daily",))
    now = _utc()
    sched.tick(now)

    since, until, _ = agg.calls[0]
    assert datetime.fromisoformat(since) == now - timedelta(days=1)
    assert datetime.fromisoformat(until) == now


# ── 실패 이월: 저장 없음 + 상태 갱신 + 다음 주기 동일 윈도우 재시도 ──


def test_failure_skips_save_and_updates_status(tmp_path: Path) -> None:
    provider = _FakeProvider()
    provider.failures = 1
    sched = _scheduler(tmp_path, provider=provider, periods=("daily",))
    now = _utc()

    assert sched.tick(now) == []
    assert sched.store.list() == []  # 빈 보고서·플레이스홀더 저장 금지

    status = sched.status()["daily"]
    assert status["failure_count"] == 1
    assert status["last_run"] == now.isoformat()
    assert status["last_success"] is None


def test_failure_carries_window_to_next_tick(tmp_path: Path) -> None:
    provider = _FakeProvider()
    provider.failures = 1
    agg = _FakeAggregator()
    sched = _scheduler(tmp_path, provider=provider, aggregator=agg, periods=("daily",))
    first = _utc()

    assert sched.tick(first) == []
    first_since = agg.calls[0][0]

    second = first + timedelta(days=1, seconds=1)
    saved = sched.tick(second)
    assert len(saved) == 1
    # 실패한 윈도우 시작과 동일한 since — 유실 없이 이월된다.
    assert agg.calls[1][0] == first_since
    assert sched.status()["daily"]["failure_count"] == 0
    assert sched.status()["daily"]["last_success"] == second.isoformat()


def test_status_exposes_run_state_keys(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path, periods=("daily", "weekly"))
    state = sched.status()

    assert set(state) == {"daily", "weekly"}
    for period_state in state.values():
        assert set(period_state) >= {"last_run", "last_success", "next_run", "failure_count"}


# ── timeout clamp (T1 리뷰 M-1 이관: 비정상 timeout의 스케줄러 hang 방지) ──


@pytest.mark.parametrize("bad", [None, 0, -5, "abc", float("nan"), float("inf")])
def test_clamp_timeout_maps_abnormal_to_default(bad) -> None:
    from src.reports.scheduler import DEFAULT_TIMEOUT_SEC, clamp_timeout_sec

    assert clamp_timeout_sec(bad) == DEFAULT_TIMEOUT_SEC


def test_clamp_timeout_bounds_finite_values() -> None:
    from src.reports.scheduler import MAX_TIMEOUT_SEC, MIN_TIMEOUT_SEC, clamp_timeout_sec

    assert clamp_timeout_sec(5.0) == 5.0
    assert clamp_timeout_sec(0.2) == MIN_TIMEOUT_SEC
    assert clamp_timeout_sec(MAX_TIMEOUT_SEC + 1000) == MAX_TIMEOUT_SEC
    assert clamp_timeout_sec(10**9) == MAX_TIMEOUT_SEC


# ── 빌더: 기본 비활성·base_url 기본값·실저장소 주입 ──


def test_builder_returns_none_when_disabled() -> None:
    from src.reports.scheduler import build_report_scheduler

    assert build_report_scheduler({}) is None
    assert build_report_scheduler({"reports": {"enabled": False}}) is None


def test_builder_defaults_base_url_to_loopback(tmp_path: Path) -> None:
    from src.reports.scheduler import build_report_scheduler

    sched = build_report_scheduler(
        {
            "models": {"report_llm": {"model_name": "llama3.1"}},
            "reports": {"enabled": True},
        },
        report_store_path=str(tmp_path / "r.db"),
    )
    assert sched is not None
    assert sched.provider.base_url == "http://127.0.0.1:11434"
    sched.close()


def test_builder_rejects_empty_model_fail_closed(tmp_path: Path) -> None:
    from src.reports.scheduler import build_report_scheduler

    with pytest.raises(ValueError):
        build_report_scheduler(
            {
                "models": {"report_llm": {"model_name": "  "}},
                "reports": {"enabled": True},
            },
            report_store_path=str(tmp_path / "r.db"),
        )


def test_builder_rejects_non_local_endpoint(tmp_path: Path) -> None:
    from src.reports.scheduler import build_report_scheduler

    with pytest.raises(ValueError):
        build_report_scheduler(
            {
                "models": {
                    "report_llm": {
                        "model_name": "llama3.1",
                        "base_url": "https://api.external.example.com",
                    }
                },
                "reports": {"enabled": True},
            },
            report_store_path=str(tmp_path / "r.db"),
        )


def test_builder_injects_real_stores_without_substitution(tmp_path: Path) -> None:
    """데몬 경로 실저장소 주입 증명 — None·스텁 치환 금지 (P2 C-1 교훈)."""
    from src.reports.scheduler import build_report_scheduler
    from src.reports.store import ReportStore

    event_repo, obs_store, incident_store = object(), object(), object()
    store = ReportStore(tmp_path / "real.db")
    sched = build_report_scheduler(
        {
            "models": {"report_llm": {"model_name": "llama3.1"}},
            "reports": {"enabled": True},
        },
        event_repository=event_repo,
        observation_store=obs_store,
        incident_store=incident_store,
        report_store=store,
    )
    assert sched is not None
    try:
        assert sched.aggregator.event_repo is event_repo
        assert sched.aggregator.observation_store is obs_store
        assert sched.aggregator.incident_store is incident_store
        assert sched.store is store
    finally:
        sched.close()


# ── 데몬 경로 기동 회귀: run.py 배선 1곳 고정 ──


def test_run_wires_scheduler_at_single_site() -> None:
    """C-1 재발 방지 — 데몬 기동 경로의 스케줄러 배선이 정확히 1곳이다."""
    repo = Path(__file__).resolve().parents[1]
    text = (repo / "src" / "run.py").read_text(encoding="utf-8")
    # import 1행 + 기동 호출 1곳 = 정확히 2회 언급.
    assert text.count("build_report_scheduler") == 2
    # M-4: 문자열 카운트만으로는 종료 누락(I-1)을 못 잡는다 — 기동·종료 호출을 명시 검증.
    assert "report_scheduler.start()" in text
    assert "report_scheduler.close()" in text


def test_daemon_injects_scheduler_into_api_app() -> None:
    """P3-T4 R5: 데몬이 실 스케줄러를 api_app.state에 주입한다 (live status 경로).

    주입이 없으면 /api/reports status는 유도값(live:false) 고정이 된다 (C-1급 갭).
    """
    import sys
    import threading
    import time
    import types
    from contextlib import ExitStack
    from unittest.mock import Mock, patch

    run_mod = pytest.importorskip("src.run")

    class _RunningServer:
        started = True

        def __init__(self) -> None:
            self.run_entered = threading.Event()
            self.should_exit = False

        def run(self) -> None:
            self.run_entered.set()
            while not self.should_exit:
                time.sleep(0.01)

    server = _RunningServer()
    pipeline, alert_manager, config = Mock(), Mock(), Mock()
    scheduler = Mock()
    api_app_instance = Mock()
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.Config = Mock(return_value=Mock())
    uvicorn.Server = Mock(return_value=server)
    api_app = types.ModuleType("src.api.app")
    api_app.create_app = Mock(return_value=api_app_instance)

    run_mod._shutdown.clear()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"uvicorn": uvicorn, "src.api.app": api_app}))
            stack.enter_context(patch.object(run_mod.ConfigLoader, "load_env"))
            stack.enter_context(patch.object(run_mod, "AppConfig", return_value=config))
            stack.enter_context(
                patch.object(run_mod, "build_pipeline", return_value=(pipeline, alert_manager, Mock()))
            )
            stack.enter_context(patch.object(run_mod, "CameraRepository", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "CredentialStore", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "RtspProbe", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "_register_cameras", return_value=1))
            stack.enter_context(patch.object(run_mod.signal, "signal"))
            stack.enter_context(patch.object(run_mod, "_sd_notify"))
            stack.enter_context(
                patch("src.reports.scheduler.build_report_scheduler", return_value=scheduler)
            )
            pipeline.start.side_effect = lambda: run_mod._shutdown.set()
            assert run_mod.main() == 0
        assert api_app_instance.state.report_scheduler is scheduler
    finally:
        run_mod._shutdown.clear()


def test_daemon_misconfigured_reports_keeps_pipeline_running() -> None:
    """P3-T4 R8 I-1: 보고 오설정(enabled+빈 모델)은 데몬을 죽이지 않는다.

    빌더 ValueError는 경고 후 격리 — 파이프라인 정상 기동(exit 0),
    스케줄러 None 주입(유도값 fallback), start/close 미호출.
    """
    import sys
    import threading
    import time
    import types
    from contextlib import ExitStack
    from unittest.mock import Mock, patch

    run_mod = pytest.importorskip("src.run")

    class _RunningServer:
        started = True

        def __init__(self) -> None:
            self.run_entered = threading.Event()
            self.should_exit = False

        def run(self) -> None:
            self.run_entered.set()
            while not self.should_exit:
                time.sleep(0.01)

    server = _RunningServer()
    pipeline, alert_manager, config = Mock(), Mock(), Mock()
    api_app_instance = Mock()
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.Config = Mock(return_value=Mock())
    uvicorn.Server = Mock(return_value=server)
    api_app = types.ModuleType("src.api.app")
    api_app.create_app = Mock(return_value=api_app_instance)

    run_mod._shutdown.clear()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"uvicorn": uvicorn, "src.api.app": api_app}))
            stack.enter_context(patch.object(run_mod.ConfigLoader, "load_env"))
            stack.enter_context(patch.object(run_mod, "AppConfig", return_value=config))
            stack.enter_context(
                patch.object(run_mod, "build_pipeline", return_value=(pipeline, alert_manager, Mock()))
            )
            stack.enter_context(patch.object(run_mod, "CameraRepository", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "CredentialStore", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "RtspProbe", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "_register_cameras", return_value=1))
            stack.enter_context(patch.object(run_mod.signal, "signal"))
            stack.enter_context(patch.object(run_mod, "_sd_notify"))
            stack.enter_context(
                patch(
                    "src.reports.scheduler.build_report_scheduler",
                    side_effect=ValueError("report model_name is empty"),
                )
            )
            pipeline.start.side_effect = lambda: run_mod._shutdown.set()
            assert run_mod.main() == 0
        pipeline.start.assert_called_once_with()
        assert api_app_instance.state.report_scheduler is None
    finally:
        run_mod._shutdown.clear()


def test_daemon_shutdown_closes_scheduler() -> None:
    """I-1: 데몬 종료 시 스케줄러 close 실호출 (정상 종료 finally 경로)."""
    import sys
    import threading
    import time
    import types
    from contextlib import ExitStack
    from unittest.mock import Mock, patch

    run_mod = pytest.importorskip("src.run")

    class _RunningServer:
        started = True

        def __init__(self) -> None:
            self.run_entered = threading.Event()
            self.should_exit = False

        def run(self) -> None:
            self.run_entered.set()
            while not self.should_exit:
                time.sleep(0.01)

    server = _RunningServer()
    pipeline, alert_manager, config = Mock(), Mock(), Mock()
    scheduler = Mock()
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.Config = Mock(return_value=Mock())
    uvicorn.Server = Mock(return_value=server)
    api_app = types.ModuleType("src.api.app")
    api_app.create_app = Mock(return_value=Mock())

    run_mod._shutdown.clear()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"uvicorn": uvicorn, "src.api.app": api_app}))
            stack.enter_context(patch.object(run_mod.ConfigLoader, "load_env"))
            stack.enter_context(patch.object(run_mod, "AppConfig", return_value=config))
            stack.enter_context(
                patch.object(run_mod, "build_pipeline", return_value=(pipeline, alert_manager, Mock()))
            )
            stack.enter_context(patch.object(run_mod, "CameraRepository", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "CredentialStore", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "RtspProbe", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "_register_cameras", return_value=1))
            stack.enter_context(patch.object(run_mod.signal, "signal"))
            stack.enter_context(patch.object(run_mod, "_sd_notify"))
            stack.enter_context(
                patch("src.reports.scheduler.build_report_scheduler", return_value=scheduler)
            )
            pipeline.start.side_effect = lambda: run_mod._shutdown.set()
            result = run_mod.main()
        assert result == 0
        scheduler.start.assert_called_once_with()
        scheduler.close.assert_called_once_with()
    finally:
        run_mod._shutdown.clear()


def test_daemon_early_return_still_closes_scheduler() -> None:
    """I-1: API 초기화 실패 조기반환 경로에서도 스케줄러 close 실호출."""
    import sys
    import types
    from contextlib import ExitStack
    from unittest.mock import Mock, patch

    run_mod = pytest.importorskip("src.run")

    pipeline, alert_manager, config = Mock(), Mock(), Mock()
    scheduler = Mock()
    uvicorn = types.ModuleType("uvicorn")
    uvicorn.Config = Mock(return_value=Mock())
    uvicorn.Server = Mock(return_value=Mock())
    api_app = types.ModuleType("src.api.app")
    api_app.create_app = Mock(side_effect=RuntimeError("bind failed"))

    run_mod._shutdown.clear()
    try:
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"uvicorn": uvicorn, "src.api.app": api_app}))
            stack.enter_context(patch.object(run_mod.ConfigLoader, "load_env"))
            stack.enter_context(patch.object(run_mod, "AppConfig", return_value=config))
            stack.enter_context(
                patch.object(run_mod, "build_pipeline", return_value=(pipeline, alert_manager, Mock()))
            )
            stack.enter_context(patch.object(run_mod, "CameraRepository", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "CredentialStore", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "RtspProbe", return_value=Mock()))
            stack.enter_context(patch.object(run_mod, "_register_cameras", return_value=1))
            stack.enter_context(patch.object(run_mod.signal, "signal"))
            stack.enter_context(patch.object(run_mod, "_sd_notify"))
            stack.enter_context(
                patch("src.reports.scheduler.build_report_scheduler", return_value=scheduler)
            )
            assert run_mod.main() == 1
        scheduler.close.assert_called_once_with()
    finally:
        run_mod._shutdown.clear()


def test_default_config_disables_reports() -> None:
    repo = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((repo / "config" / "default.yaml").read_text(encoding="utf-8"))

    assert config["reports"]["enabled"] is False
    assert "127.0.0.1" in str(config["models"]["report_llm"].get("base_url", ""))


# ── T3 리뷰 fix round 1 (R4) ──


def test_builder_periods_all_off_returns_none_without_model_check(tmp_path: Path) -> None:
    """M-2: periods 전부 off면 모델 검증보다 먼저 inert(None) — raise 금지."""
    from src.reports.scheduler import build_report_scheduler

    assert (
        build_report_scheduler(
            {
                "models": {"report_llm": {"model_name": "  "}},
                "reports": {"enabled": True, "daily_enabled": False, "weekly_enabled": False},
            },
            report_store_path=str(tmp_path / "r.db"),
        )
        is None
    )


def test_builder_abnormal_retention_falls_back_to_default(tmp_path: Path) -> None:
    """I-2: 비정상 보존기간은 기본 90일 — 1일 과삭제(파괴적) 금지."""
    from src.reports.scheduler import DEFAULT_RETENTION_DAYS, build_report_scheduler

    assert DEFAULT_RETENTION_DAYS == 90
    for bad in ("abc", 0, -5):
        sched = build_report_scheduler(
            {
                "models": {"report_llm": {"model_name": "llama3.1"}},
                "reports": {"enabled": True, "retention_days": bad},
            },
            report_store_path=str(tmp_path / "r.db"),
        )
        assert sched is not None
        try:
            assert sched.retention_days == 90
        finally:
            sched.close()
    sched = build_report_scheduler(
        {
            "models": {"report_llm": {"model_name": "llama3.1"}},
            "reports": {"enabled": True, "retention_days": 30},
        },
        report_store_path=str(tmp_path / "r.db"),
    )
    assert sched is not None
    try:
        assert sched.retention_days == 30
    finally:
        sched.close()


def test_builder_abnormal_max_context_falls_back_to_default(tmp_path: Path) -> None:
    """M-3: 비정상 토큰 상한은 기본값 — 1토큰 강행 금지."""
    from src.reports.scheduler import DEFAULT_MAX_CONTEXT, build_report_scheduler

    for bad in ("abc", 0, -5):
        sched = build_report_scheduler(
            {
                "models": {"report_llm": {"model_name": "llama3.1"}},
                "reports": {"enabled": True, "max_context": bad},
            },
            report_store_path=str(tmp_path / "r.db"),
        )
        assert sched is not None
        try:
            assert sched.max_context == DEFAULT_MAX_CONTEXT
        finally:
            sched.close()


def test_failure_logs_full_exception_not_just_type(tmp_path: Path, caplog) -> None:
    """M-1: 실패 로그는 타입명이 아니라 예외 전체(traceback 수준)."""
    import logging

    provider = _FakeProvider()
    provider.failures = 99  # generate가 항상 RuntimeError("LLM boom") 발생
    sched = _scheduler(tmp_path, provider=provider, periods=("daily",))
    with caplog.at_level(logging.WARNING, logger="ksi_cctv.reports.scheduler"):
        sched.tick(_utc())
    assert "LLM boom" in caplog.text


def test_start_stop_joins_thread(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path, periods=("daily",))
    try:
        sched.start()
        assert sched._thread is not None
    finally:
        sched.stop()
    assert not sched._thread.is_alive()


def test_stop_warns_when_thread_survives_join(tmp_path: Path, caplog) -> None:
    """I-3: join 후 스레드가 살아있으면 경고 — '정지됨' 보장 거짓 금지."""
    import logging
    from unittest.mock import Mock

    sched = _scheduler(tmp_path, periods=("daily",))
    stuck = Mock()
    stuck.is_alive.return_value = True
    sched._thread = stuck
    with caplog.at_level(logging.WARNING, logger="ksi_cctv.reports.scheduler"):
        sched.stop()
    stuck.join.assert_called_once()
    assert "did not stop" in caplog.text


def test_close_stops_thread_and_closes_store(tmp_path: Path) -> None:
    sched = _scheduler(tmp_path, periods=("daily",))
    sched.start()
    sched.close()
    assert not sched._thread.is_alive()
    assert sched.store._connection is None
