"""Regression: first record() must defer the automatic retention sweep.

Root cause: both SQLite stores initialized ``_last_prune = 0.0`` while
``_prune_if_due()`` compares ``time.monotonic() - _last_prune`` against
``prune_interval_sec`` (default 21600s). On any host with uptime beyond the
interval, the very first ``record()`` triggered an immediate ``prune()`` and
deleted just-inserted rows when fixtures predated the retention cutoff.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from src.storage.sqlite_events import SQLiteEventRepository
from src.storage.sqlite_observations import SQLiteObservationStore

BASE_MONOTONIC = 1_000_000_000.0


def _old_and_new():
    now = datetime.now(timezone.utc)
    return now - timedelta(days=60), now


def test_observation_store_defers_first_sweep(tmp_path):
    old = datetime.now() - timedelta(days=60)
    current = datetime.now()
    with patch("time.monotonic", return_value=BASE_MONOTONIC):
        store = SQLiteObservationStore(tmp_path / "observations.db")
        store.record("cam", old, {})
        # First record must not sweep: the just-inserted bucket survives.
        remaining = store.query("cam")
        assert len(remaining) == 1

        # After the interval elapses, the sweep fires and removes only old rows.
        with patch("time.monotonic", return_value=BASE_MONOTONIC + 21601.0):
            store.record("cam", current, {})
        remaining = store.query("cam")
        store.close()

    assert len(remaining) == 1
    assert remaining[0]["bucket_start"].startswith(current.strftime("%Y-%m-%d"))


def test_event_repository_defers_first_sweep(tmp_path):
    old, now = _old_and_new()
    with patch("time.monotonic", return_value=BASE_MONOTONIC):
        repo = SQLiteEventRepository(tmp_path / "events.db")
        repo.record({"event_id": "old-1", "timestamp": old.isoformat()})
        # First record must not sweep: the just-inserted row survives.
        assert [item["event_id"] for item in repo.list()] == ["old-1"]

        # After the interval elapses, the sweep fires and removes only old rows.
        with patch("time.monotonic", return_value=BASE_MONOTONIC + 21601.0):
            repo.record({"event_id": "new-1", "timestamp": now.isoformat()})
        event_ids = [item["event_id"] for item in repo.list()]
        repo.close()

    assert event_ids == ["new-1"]
