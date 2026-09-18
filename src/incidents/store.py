"""Durable operator incident state kept separate from immutable AI events."""
from __future__ import annotations

import builtins
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.storage.schema import UnsupportedSchemaVersionError

INCIDENT_STATUSES = frozenset({"new", "acknowledged", "in_progress", "resolved"})
INCIDENT_DISPOSITIONS = frozenset({"confirmed", "false_positive", "duplicate", "test"})
INCIDENT_CATEGORIES = frozenset({"safety", "camera_health"})
INCIDENT_SCHEMA_VERSION = 1


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IncidentStore:
    """Persist mutable operator handling without changing event evidence records."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def list(
        self,
        *,
        status: str | None = None,
        category: str | None = None,
        unresolved: bool = False,
        limit: int = 200,
    ) -> builtins.list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 1000))
        sql = self._summary_select()
        params: builtins.list[Any] = []
        where: builtins.list[str] = []
        if status:
            where.append("status = ?")
            params.append(status)
        elif unresolved:
            where.append("status != 'resolved'")
        if category:
            where.append("category = ?")
            params.append(category)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(safe_limit)
        with self._lock:
            rows = self._connect().execute(sql, tuple(params)).fetchall()
        return [self._incident_row(row) for row in rows]

    def get(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connect().execute(
                self._summary_select() + " WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._incident_row(row) if row is not None else None

    def ensure(
        self,
        event_id: str,
        *,
        category: str = "safety",
        camera_id: str | None = None,
        source_event_type: str | None = None,
        source_event_alert_level: str | None = None,
    ) -> dict[str, Any]:
        """Create a newly raised incident once without overwriting operator state."""
        if category not in INCIDENT_CATEGORIES:
            raise ValueError("unsupported incident category")
        now = _now()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO incidents(
                    event_id, category, camera_id, source_event_type, source_event_alert_level, status,
                    disposition, acknowledged_at, resolved_at, recovered_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'new', NULL, NULL, NULL, NULL, ?)
                ON CONFLICT(event_id) DO NOTHING
                """,
                (event_id, category, camera_id, source_event_type, source_event_alert_level, now),
            )
        incident = self.get(event_id)
        assert incident is not None
        return incident

    def mark_recovered(self, camera_id: str, recovery_event_id: str) -> dict[str, Any] | None:
        """Record recovery context without automatically resolving operator work."""
        connection = self._connect()
        now = _now()
        with self._lock, connection:
            row = connection.execute(
                """
                SELECT event_id FROM incidents
                WHERE category = 'camera_health' AND camera_id = ?
                  AND status != 'resolved' AND recovered_at IS NULL
                ORDER BY updated_at DESC LIMIT 1
                """,
                (camera_id,),
            ).fetchone()
            if row is None:
                return None
            event_id = str(row[0])
            connection.execute(
                "UPDATE incidents SET recovered_at = ?, recovery_event_id = ?, updated_at = ? WHERE event_id = ?",
                (now, recovery_event_id, now, event_id),
            )
        return self.get(event_id)

    def backfill_source_alert_levels(self, levels_by_event_id: dict[str, str]) -> None:
        """Persist durable event severity for legacy incidents without changing operator chronology."""
        updates = [
            (alert_level, event_id)
            for event_id, alert_level in levels_by_event_id.items()
            if isinstance(event_id, str) and event_id and isinstance(alert_level, str) and alert_level
        ]
        if not updates:
            return
        with self._lock, self._connect() as connection:
            connection.executemany(
                """
                UPDATE incidents SET source_event_alert_level = ?
                WHERE event_id = ? AND source_event_alert_level IS NULL
                """,
                updates,
            )

    def act(
        self,
        event_id: str,
        *,
        status: str,
        disposition: str | None = None,
        note: str = "",
        actor: str | None = None,
    ) -> dict[str, Any]:
        if status not in INCIDENT_STATUSES:
            raise ValueError("unsupported incident status")
        if disposition is not None and disposition not in INCIDENT_DISPOSITIONS:
            raise ValueError("unsupported incident disposition")
        if status == "resolved" and disposition is None:
            raise ValueError("resolved incidents require a disposition")

        now = _now()
        connection = self._connect()
        with self._lock, connection:
            current = connection.execute(
                "SELECT status, disposition, acknowledged_at FROM incidents WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            previous_status = str(current[0]) if current is not None else "new"
            previous_disposition = str(current[1]) if current is not None and current[1] else None
            acknowledged_at = (
                str(current[2])
                if current is not None and current[2]
                else now if status in {"acknowledged", "in_progress", "resolved"} else None
            )
            resolved_at = now if status == "resolved" else None
            selected_disposition = disposition if disposition is not None else previous_disposition
            connection.execute(
                """
                INSERT INTO incidents(event_id, status, disposition, acknowledged_at, resolved_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    status = excluded.status,
                    disposition = excluded.disposition,
                    acknowledged_at = excluded.acknowledged_at,
                    resolved_at = excluded.resolved_at,
                    updated_at = excluded.updated_at
                """,
                (event_id, status, selected_disposition, acknowledged_at, resolved_at, now),
            )
            connection.execute(
                """
                INSERT INTO incident_actions(event_id, previous_status, status, disposition, note, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (event_id, previous_status, status, selected_disposition, note, actor, now),
            )
        incident = self.get(event_id)
        assert incident is not None
        incident["actions"] = self.actions(event_id)
        return incident

    def actions(self, event_id: str, *, limit: int = 20) -> builtins.list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 100))
        with self._lock:
            rows = self._connect().execute(
                """
                SELECT previous_status, status, disposition, note, actor, created_at
                FROM incident_actions WHERE event_id = ?
                ORDER BY action_id DESC LIMIT ?
                """,
                (event_id, safe_limit),
            ).fetchall()
        return [
            {
                "previous_status": str(row[0]),
                "status": str(row[1]),
                "disposition": str(row[2]) if row[2] else None,
                "note": str(row[3]),
                "actor": str(row[4]) if row[4] else None,
                "created_at": str(row[5]),
            }
            for row in rows
        ]

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def validate_schema(self) -> None:
        """Eagerly fail startup when incident state was written by newer code."""
        self._connect()

    def _connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is not None:
                return self._connection
            self.path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.path.parent, 0o700)
            connection = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS incident_schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                applied = {
                    int(row[0])
                    for row in connection.execute("SELECT version FROM incident_schema_migrations")
                }
                newest_version = max(applied, default=0)
                if newest_version > INCIDENT_SCHEMA_VERSION:
                    raise UnsupportedSchemaVersionError(
                        "incident database", newest_version, INCIDENT_SCHEMA_VERSION
                    )
                with connection:
                    connection.executescript(
                        """
                    CREATE TABLE IF NOT EXISTS incidents (
                        event_id TEXT PRIMARY KEY,
                        category TEXT NOT NULL DEFAULT 'safety',
                        camera_id TEXT,
                        source_event_type TEXT,
                        source_event_alert_level TEXT,
                        status TEXT NOT NULL,
                        disposition TEXT,
                        acknowledged_at TEXT,
                        resolved_at TEXT,
                        recovered_at TEXT,
                        recovery_event_id TEXT,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_incidents_status_updated
                        ON incidents(status, updated_at DESC);
                    CREATE TABLE IF NOT EXISTS incident_actions (
                        action_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        event_id TEXT NOT NULL,
                        previous_status TEXT NOT NULL,
                        status TEXT NOT NULL,
                        disposition TEXT,
                        note TEXT NOT NULL,
                        actor TEXT,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_incident_actions_event
                        ON incident_actions(event_id, action_id DESC);
                        """
                    )
                    existing_columns = {
                        str(row[1]) for row in connection.execute("PRAGMA table_info(incidents)")
                    }
                    for name, definition in (
                        ("category", "TEXT NOT NULL DEFAULT 'safety'"),
                        ("camera_id", "TEXT"),
                        ("source_event_type", "TEXT"),
                        ("source_event_alert_level", "TEXT"),
                        ("recovered_at", "TEXT"),
                        ("recovery_event_id", "TEXT"),
                    ):
                        if name not in existing_columns:
                            connection.execute(f"ALTER TABLE incidents ADD COLUMN {name} {definition}")
                    existing_action_columns = {
                        str(row[1]) for row in connection.execute("PRAGMA table_info(incident_actions)")
                    }
                    if "actor" not in existing_action_columns:
                        connection.execute("ALTER TABLE incident_actions ADD COLUMN actor TEXT")
                    if INCIDENT_SCHEMA_VERSION not in applied:
                        connection.execute(
                            "INSERT INTO incident_schema_migrations(version, applied_at) VALUES (?, ?)",
                            (INCIDENT_SCHEMA_VERSION, _now()),
                        )
                os.chmod(self.path, 0o600)
            except Exception:
                connection.close()
                raise
            self._connection = connection
            return connection

    @staticmethod
    def _incident_row(row: Any) -> dict[str, Any]:
        return {
            "event_id": str(row[0]),
            "category": str(row[1]),
            "camera_id": str(row[2]) if row[2] else None,
            "source_event_type": str(row[3]) if row[3] else None,
            "source_event_alert_level": str(row[4]) if row[4] else None,
            "status": str(row[5]),
            "disposition": str(row[6]) if row[6] else None,
            "acknowledged_at": str(row[7]) if row[7] else None,
            "resolved_at": str(row[8]) if row[8] else None,
            "recovered_at": str(row[9]) if row[9] else None,
            "recovery_event_id": str(row[10]) if row[10] else None,
            "updated_at": str(row[11]),
        }

    @staticmethod
    def _summary_select() -> str:
        return (
            "SELECT event_id, category, camera_id, source_event_type, source_event_alert_level, status, disposition, "
            "acknowledged_at, resolved_at, recovered_at, recovery_event_id, updated_at FROM incidents"
        )
