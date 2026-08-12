from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.storage.schema import UnsupportedSchemaVersionError
from src.storage.sqlite_events import SQLiteEventRepository


def event(
    event_id: str,
    *,
    timestamp: str = "2026-07-26T00:00:00+00:00",
    camera_id: str = "cam-01",
    alert_level: str = "high",
    alert_state: str = "provisional",
) -> dict:
    return {
        "event_id": event_id,
        "timestamp": timestamp,
        "camera_id": camera_id,
        "event_type": "intrusion",
        "alert_level": alert_level,
        "alert_state": alert_state,
        "description": "masked at alert-manager boundary",
    }


class TestSQLiteEventRepository(unittest.TestCase):
    def test_record_and_query_survive_restart_with_latest_lifecycle_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.db"
            repo = SQLiteEventRepository(path)
            first = repo.record(event("evt-1", alert_state="provisional"))
            repo.record(event("evt-1", alert_state="confirmed"))
            third = repo.record(event("evt-2", camera_id="cam-02", alert_level="critical"))
            repo.close()

            reopened = SQLiteEventRepository(path)
            latest = reopened.list(limit=10)
            self.assertEqual([item["event_id"] for item in latest], ["evt-2", "evt-1"])
            self.assertEqual(latest[1]["alert_state"], "confirmed")
            self.assertEqual(reopened.list_after_stream_id(first), [
                event("evt-1", alert_state="confirmed"),
                event("evt-2", camera_id="cam-02", alert_level="critical"),
            ])
            self.assertEqual(reopened.latest_stream_id(), third)
            reopened.close()

    def test_filters_pagination_and_all_lifecycle_revisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db", event_timezone=timezone.utc)
            repo.record(event("evt-1", timestamp="2026-07-24T00:00:00+00:00", camera_id="a"))
            repo.record(event("evt-2", timestamp="2026-07-25T00:00:00+00:00", camera_id="b", alert_level="critical"))
            repo.record(event("evt-2", timestamp="2026-07-25T00:01:00+00:00", camera_id="b", alert_level="critical", alert_state="confirmed"))
            repo.record(event("evt-3", timestamp="2026-07-26T00:00:00+00:00", camera_id="a"))

            self.assertEqual([item["event_id"] for item in repo.list(camera_id="a")], ["evt-3", "evt-1"])
            self.assertEqual([item["event_id"] for item in repo.list(alert_level="critical")], ["evt-2"])
            self.assertEqual([item["event_id"] for item in repo.list(limit=1, offset=1)], ["evt-2"])
            all_revisions = repo.list(limit=10, latest_only=False)
            self.assertEqual(len(all_revisions), 4)
            repo.close()

    def test_get_returns_latest_state_for_exact_event_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db")
            repo.record(event("evt-1", alert_state="provisional"))
            repo.record(event("evt-1", alert_state="confirmed"))
            repo.record(event("evt-10", alert_state="dismissed"))

            self.assertEqual(repo.get("evt-1")["alert_state"], "confirmed")
            self.assertIsNone(repo.get("evt-unknown"))
            repo.close()

    def test_normalizes_naive_offset_and_zulu_timestamps_for_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = SQLiteEventRepository(Path(tmp) / "events.db", event_timezone=timezone.utc)
            repo.record(event("naive", timestamp="2026-07-25T00:00:00"))
            repo.record(event("offset", timestamp="2026-07-25T09:30:00+09:00"))
            repo.record(event("zulu", timestamp="2026-07-25T00:45:00Z"))

            filtered = repo.list(
                since="2026-07-25T00:15:00Z",
                until="2026-07-25T00:40:00Z",
            )

            self.assertEqual([item["event_id"] for item in filtered], ["offset"])
            self.assertEqual(filtered[0]["timestamp"], "2026-07-25T00:30:00+00:00")
            repo.close()

    def test_migrates_pre_normalization_timestamp_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.db"
            kst = timezone(timedelta(hours=9))
            repo = SQLiteEventRepository(path, event_timezone=kst)
            stream_id = repo.record(event("legacy"))
            legacy_payload = event("legacy", timestamp="2026-07-25T09:30:00")
            connection = repo._connect()
            with connection:
                connection.execute(
                    "UPDATE event_records SET timestamp = ?, payload = ? WHERE stream_id = ?",
                    (
                        legacy_payload["timestamp"],
                        json.dumps(legacy_payload),
                        stream_id,
                    ),
                )
                connection.execute("DELETE FROM event_schema_migrations WHERE version = 2")
            repo.close()

            migrated = SQLiteEventRepository(path, event_timezone=kst)
            item = migrated.list()[0]

            self.assertEqual(item["timestamp"], "2026-07-25T00:30:00+00:00")
            migrated.close()

    def test_retention_and_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private" / "events.db"
            repo = SQLiteEventRepository(path, retention_days=1)
            # 자동 prune을 잠시 미뤄 explicit prune 동작을 검증한다.
            repo._last_prune = time.monotonic()
            repo.record(event("old", timestamp="2020-01-01T00:00:00+00:00"))
            repo.record(event("new", timestamp="2026-07-26T00:00:00+00:00"))
            deleted = repo.prune(datetime(2026, 7, 26, tzinfo=timezone.utc))
            self.assertEqual(deleted, 1)
            self.assertEqual([item["event_id"] for item in repo.list()], ["new"])
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            repo.close()

    def test_rejects_newer_schema_version_without_caching_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.db"
            connection = sqlite3.connect(path)
            connection.execute(
                "CREATE TABLE event_schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO event_schema_migrations(version, applied_at) VALUES (99, 'now')"
            )
            connection.commit()
            repo = SQLiteEventRepository(path)

            with patch(
                "src.storage.sqlite_events.sqlite3.connect", return_value=connection
            ), self.assertRaisesRegex(
                UnsupportedSchemaVersionError, "99.*supported version 2"
            ):
                repo.list()

            self.assertIsNone(repo._connection)
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")
