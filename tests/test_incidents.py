from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.alerts.alert_manager import AlertManager
from src.incidents import IncidentStore
from src.storage.schema import UnsupportedSchemaVersionError


def test_incident_actions_survive_restart_and_keep_notes_out_of_summary():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.db"
        store = IncidentStore(path)
        acknowledged = store.act("event-1", status="acknowledged", note="현장 담당자에게 확인 요청")
        resolved = store.act(
            "event-1",
            status="resolved",
            disposition="false_positive",
            note="반사광으로 판정",
        )
        store.close()

        reopened = IncidentStore(path)
        summary = reopened.get("event-1")
        actions = reopened.actions("event-1")

    assert acknowledged["status"] == "acknowledged"
    assert resolved["resolved_at"] is not None
    assert summary is not None
    assert summary["event_id"] == "event-1"
    assert summary["category"] == "safety"
    assert summary["status"] == "resolved"
    assert summary["disposition"] == "false_positive"
    assert summary["acknowledged_at"] == acknowledged["acknowledged_at"]
    assert summary["resolved_at"] == resolved["resolved_at"]
    assert [item["note"] for item in actions] == ["반사광으로 판정", "현장 담당자에게 확인 요청"]


def test_resolved_incident_requires_disposition():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")
        with pytest.raises(ValueError, match="require a disposition"):
            store.act("event-1", status="resolved")


def test_ensure_creates_new_incident_without_overwriting_operator_progress():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")

        created = store.ensure("event-1")
        store.act("event-1", status="in_progress", note="현장 점검 중")
        existing = store.ensure("event-1")

    assert created["status"] == "new"
    assert existing["status"] == "in_progress"


def test_ensure_persists_source_alert_level_for_live_risk_priority():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")
        incident = store.ensure(
            "event-1",
            camera_id="camera-1",
            source_event_type="fire",
            source_event_alert_level="critical",
        )

    assert incident["source_event_alert_level"] == "critical"


def test_existing_incident_database_gains_source_alert_level_column():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE incidents (
                    event_id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'safety', camera_id TEXT,
                    source_event_type TEXT, status TEXT NOT NULL, disposition TEXT, acknowledged_at TEXT,
                    resolved_at TEXT, recovered_at TEXT, recovery_event_id TEXT, updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO incidents VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("legacy-1", "safety", "camera-1", "fire", "new", None, None, None, None, None, "2026-07-30T00:00:00Z"),
            )

        store = IncidentStore(path)
        legacy = store.get("legacy-1")

    assert legacy is not None
    assert legacy["source_event_alert_level"] is None


def test_future_incident_schema_fails_closed_before_table_changes():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE incident_schema_migrations "
                "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO incident_schema_migrations(version, applied_at) VALUES (2, ?)",
                ("2026-08-02T00:00:00Z",),
            )

        store = IncidentStore(path)
        with pytest.raises(UnsupportedSchemaVersionError, match="incident database schema version 2"):
            store.validate_schema()
        with sqlite3.connect(path) as connection:
            tables = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }

    assert "incidents" not in tables


def test_backfill_source_alert_levels_preserves_operator_update_time():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")
        created = store.ensure("legacy-1", camera_id="camera-1")
        store.backfill_source_alert_levels({"legacy-1": "critical"})
        backfilled = store.get("legacy-1")

    assert backfilled is not None
    assert backfilled["source_event_alert_level"] == "critical"
    assert backfilled["updated_at"] == created["updated_at"]


def test_camera_health_recovery_records_context_without_resolving_incident():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")
        opened = store.ensure(
            "disconnect-1",
            category="camera_health",
            camera_id="cam-01",
            source_event_type="camera_disconnect",
        )
        recovered = store.mark_recovered("cam-01", "recovery-1")

    assert opened["category"] == "camera_health"
    assert recovered is not None
    assert recovered["status"] == "new"
    assert recovered["recovery_event_id"] == "recovery-1"
    assert recovered["recovered_at"] is not None


def test_list_unresolved_excludes_resolved_incidents():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")
        store.ensure("open-1")
        store.ensure("closed-1")
        store.act("closed-1", status="resolved", disposition="test")

        incidents = store.list(unresolved=True)

    assert [incident["event_id"] for incident in incidents] == ["open-1"]


def test_alert_manager_creates_and_updates_camera_health_incidents():
    class Events:
        def __init__(self):
            self.records: list[dict] = []

        def record(self, event: dict) -> None:
            self.records.append(event)

    class Incidents:
        def __init__(self):
            self.ensured: list[tuple[str, dict]] = []
            self.recovered: list[tuple[str, str]] = []

        def ensure(self, event_id: str, **kwargs: object) -> None:
            self.ensured.append((event_id, kwargs))

        def mark_recovered(self, camera_id: str, event_id: str) -> None:
            self.recovered.append((camera_id, event_id))

    manager = object.__new__(AlertManager)
    manager.event_repository = Events()
    manager.incident_store = Incidents()
    manager.durable_store_status = None

    manager._persist_event({
        "event_id": "disconnect-1",
        "event_type": "camera_disconnect",
        "camera_id": "cam-01",
        "alert_level": "high",
    })
    manager._persist_event({
        "event_id": "recovery-1",
        "event_type": "camera_recovered",
        "camera_id": "cam-01",
        "alert_level": "low",
    })

    assert manager.incident_store.ensured == [
        ("disconnect-1", {
            "category": "camera_health",
            "camera_id": "cam-01",
            "source_event_type": "camera_disconnect",
            "source_event_alert_level": "high",
        }),
    ]
    assert manager.incident_store.recovered == [("cam-01", "recovery-1")]


def test_act_records_actor_and_returns_it_in_actions():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")
        store.act("event-actor", status="acknowledged", note="확인", actor="operator-1")
        actions = store.actions("event-actor")

    assert len(actions) == 1
    assert actions[0]["actor"] == "operator-1"


def test_act_without_actor_defaults_to_none():
    with tempfile.TemporaryDirectory() as tmp:
        store = IncidentStore(Path(tmp) / "events.db")
        incident = store.act("event-no-actor", status="acknowledged", note="확인")
        actions = store.actions("event-no-actor")

    assert len(actions) == 1
    assert actions[0]["actor"] is None
    assert incident["actions"][0]["actor"] is None


def test_legacy_action_table_gains_actor_column_on_migration():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "events.db"
        with sqlite3.connect(path) as connection:
            connection.execute(
                """
                CREATE TABLE incidents (
                    event_id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'safety', camera_id TEXT,
                    source_event_type TEXT, status TEXT NOT NULL, disposition TEXT, acknowledged_at TEXT,
                    resolved_at TEXT, recovered_at TEXT, recovery_event_id TEXT, updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE incident_actions (
                    action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    previous_status TEXT NOT NULL,
                    status TEXT NOT NULL,
                    disposition TEXT,
                    note TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "INSERT INTO incident_actions(event_id, previous_status, status, disposition, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy-1", "new", "acknowledged", None, "구 스키마 기록", "2026-09-18T00:00:00+00:00"),
            )

        store = IncidentStore(path)
        actions = store.actions("legacy-1")
        store.act("legacy-1", status="in_progress", note="이관 후 조치", actor="operator-2")
        migrated = store.actions("legacy-1")
        store.close()

    assert actions[0]["actor"] is None
    assert [item["actor"] for item in migrated] == ["operator-2", None]
