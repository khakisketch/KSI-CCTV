"""Durable SQLite event repository.

The alert log remains the live SSE tail source for backward compatibility. This
repository is the durable query source: it preserves each lifecycle revision of
an event, survives process restarts, and applies retention independently of log
rotation.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .schema import UnsupportedSchemaVersionError

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS event_records (
            stream_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            revision INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            camera_id TEXT,
            event_type TEXT,
            alert_level TEXT,
            alert_state TEXT,
            evidence_image TEXT,
            payload TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(event_id, revision)
        );
        CREATE INDEX IF NOT EXISTS idx_event_records_timestamp
            ON event_records(timestamp DESC, stream_id DESC);
        CREATE INDEX IF NOT EXISTS idx_event_records_camera_timestamp
            ON event_records(camera_id, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_event_records_type_timestamp
            ON event_records(event_type, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_event_records_alert_timestamp
            ON event_records(alert_level, timestamp DESC);
        """,
    ),
    # Normalize timestamps persisted by the initial repository implementation.
    # The data migration runs in Python because SQLite has no reliable ISO8601
    # offset parser for all forms accepted by the API.
    (2, ""),
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _site_timezone() -> tzinfo:
    configured = os.environ.get("TZ", "").strip()
    if configured:
        try:
            return ZoneInfo(configured)
        except ZoneInfoNotFoundError:
            pass
    return datetime.now().astimezone().tzinfo or timezone.utc


def _utc_datetime(value: Any, naive_timezone: tzinfo = timezone.utc) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return (
        parsed.replace(tzinfo=naive_timezone).astimezone(timezone.utc)
        if parsed.tzinfo is None
        else parsed.astimezone(timezone.utc)
    )


def _normalized_timestamp(
    value: Any,
    fallback: datetime | None = None,
    naive_timezone: tzinfo = timezone.utc,
) -> str | None:
    parsed = _utc_datetime(value, naive_timezone) or fallback
    return parsed.isoformat() if parsed is not None else None


class SQLiteEventRepository:
    """Append lifecycle revisions and query latest event state from SQLite.

    This class owns a separate migration table so it can safely share the
    observations database without colliding with ``SQLiteObservationStore``'s
    schema version numbers.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        retention_days: int = 30,
        prune_interval_sec: float = 21_600.0,
        event_timezone: tzinfo | None = None,
    ) -> None:
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))
        self.prune_interval_sec = max(1.0, float(prune_interval_sec))
        # Pipeline events historically use naive datetime.now(); interpret them in
        # the appliance's configured local timezone before persisting UTC.
        self.event_timezone = event_timezone or _site_timezone()
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()
        self._last_prune = time.monotonic()

    def record(self, event: dict[str, Any]) -> int:
        """Store one masked event revision and return its monotonic stream id."""
        payload = dict(event)
        now = _utc_now()
        event_id = str(payload.get("event_id") or self._legacy_event_id(payload))
        timestamp = _normalized_timestamp(payload.get("timestamp"), now, self.event_timezone)
        assert timestamp is not None
        payload["timestamp"] = timestamp
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        connection = self._connect()
        with self._lock, connection:
            revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(revision), 0) + 1 FROM event_records WHERE event_id = ?",
                    (event_id,),
                ).fetchone()[0]
            )
            cursor = connection.execute(
                """
                INSERT INTO event_records(
                    event_id, revision, timestamp, camera_id, event_type,
                    alert_level, alert_state, evidence_image, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    revision,
                    timestamp,
                    self._optional_text(payload.get("camera_id")),
                    self._optional_text(payload.get("event_type")),
                    self._optional_text(payload.get("alert_level")),
                    self._optional_text(payload.get("alert_state")),
                    self._optional_text(payload.get("evidence_image")),
                    serialized,
                    now.isoformat(),
                ),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return a stream id")
            stream_id = int(cursor.lastrowid)
        self._prune_if_due()
        return stream_id

    def list(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        event_type: str | None = None,
        camera_id: str | None = None,
        alert_level: str | None = None,
        since: str | None = None,
        until: str | None = None,
        latest_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Return newest-first events with filters compatible with ``list_events``."""
        safe_limit = max(1, min(int(limit), 1000))
        safe_offset = max(0, int(offset))
        where: list[str] = []
        params: list[Any] = []
        if latest_only:
            where.append(
                "stream_id IN (SELECT MAX(stream_id) FROM event_records GROUP BY event_id)"
            )
        if event_type:
            where.append("event_type = ?")
            params.append(event_type)
        if camera_id:
            where.append("camera_id = ?")
            params.append(camera_id)
        if alert_level:
            where.append("alert_level = ?")
            params.append(alert_level)
        if since:
            normalized_since = _normalized_timestamp(since, naive_timezone=self.event_timezone)
            if normalized_since is not None:
                where.append("timestamp >= ?")
                params.append(normalized_since)
        if until:
            normalized_until = _normalized_timestamp(until, naive_timezone=self.event_timezone)
            if normalized_until is not None:
                where.append("timestamp <= ?")
                params.append(normalized_until)
        sql = "SELECT payload FROM event_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC, stream_id DESC LIMIT ? OFFSET ?"
        params.extend((safe_limit, safe_offset))
        with self._lock:
            rows = self._connect().execute(sql, tuple(params)).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def get(self, event_id: str) -> dict[str, Any] | None:
        """Return the latest lifecycle state for exactly one event ID."""
        if not isinstance(event_id, str) or not event_id:
            return None
        with self._lock:
            row = self._connect().execute(
                """
                SELECT payload FROM event_records
                WHERE event_id = ?
                ORDER BY revision DESC, stream_id DESC
                LIMIT 1
                """,
                (event_id,),
            ).fetchone()
        return json.loads(str(row[0])) if row is not None else None

    def get_alert_levels(self, event_ids: builtins.list[str]) -> dict[str, str]:
        """Return latest persisted alert levels for a bounded set of incident source IDs."""
        unique_ids = list(dict.fromkeys(event_id for event_id in event_ids if isinstance(event_id, str) and event_id))[:1000]
        if not unique_ids:
            return {}
        placeholders = ", ".join("?" for _ in unique_ids)
        with self._lock:
            rows = self._connect().execute(
                f"""
                SELECT event_id, alert_level FROM event_records
                WHERE stream_id IN (
                    SELECT MAX(stream_id) FROM event_records
                    WHERE event_id IN ({placeholders})
                    GROUP BY event_id
                )
                """,
                tuple(unique_ids),
            ).fetchall()
        return {
            str(event_id): str(alert_level)
            for event_id, alert_level in rows
            if alert_level is not None and str(alert_level)
        }

    def list_after_stream_id(
        self, stream_id: int, *, limit: int = 1000
    ) -> builtins.list[dict[str, Any]]:
        """Return chronological lifecycle records after a durable cursor."""
        safe_limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._connect().execute(
                "SELECT payload FROM event_records WHERE stream_id > ? ORDER BY stream_id ASC LIMIT ?",
                (max(0, int(stream_id)), safe_limit),
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def stream_records_after(
        self, stream_id: int, *, limit: int = 1000
    ) -> builtins.list[tuple[int, dict[str, Any]]]:
        """Return durable cursor records without exposing cursor data in payloads."""
        safe_limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._connect().execute(
                "SELECT stream_id, payload FROM event_records WHERE stream_id > ? ORDER BY stream_id ASC LIMIT ?",
                (max(0, int(stream_id)), safe_limit),
            ).fetchall()
        return [(int(row[0]), json.loads(str(row[1]))) for row in rows]

    def first_stream_id(self) -> int:
        with self._lock:
            row = self._connect().execute("SELECT COALESCE(MIN(stream_id), 0) FROM event_records").fetchone()
        return int(row[0])

    def latest_stream_id(self) -> int:
        with self._lock:
            row = self._connect().execute("SELECT COALESCE(MAX(stream_id), 0) FROM event_records").fetchone()
        return int(row[0])

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (
            _utc_datetime(now, self.event_timezone) or _utc_now()
        ) - timedelta(days=self.retention_days)
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM event_records WHERE timestamp < ?",
                (cutoff.isoformat(),),
            )
        return max(0, int(cursor.rowcount))

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

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
                    "CREATE TABLE IF NOT EXISTS event_schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                applied = {
                    int(row[0])
                    for row in connection.execute("SELECT version FROM event_schema_migrations")
                }
                newest_version = max(applied, default=0)
                supported_version = max(version for version, _ in _MIGRATIONS)
                if newest_version > supported_version:
                    raise UnsupportedSchemaVersionError(
                        "event database", newest_version, supported_version
                    )
                for version, sql in _MIGRATIONS:
                    if version in applied:
                        continue
                    with connection:
                        connection.executescript(sql)
                        if version == 2:
                            self._normalize_legacy_timestamps(connection)
                        connection.execute(
                            "INSERT INTO event_schema_migrations(version, applied_at) VALUES (?, ?)",
                            (version, _utc_now().isoformat()),
                        )
                os.chmod(self.path, 0o600)
            except Exception:
                # Do not cache a partly initialized connection after a failed migration.
                connection.close()
                raise
            self._connection = connection
            return connection

    def _prune_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_prune < self.prune_interval_sec:
            return
        self.prune()
        self._last_prune = now

    def _normalize_legacy_timestamps(self, connection: sqlite3.Connection) -> None:
        rows = connection.execute("SELECT stream_id, timestamp, payload FROM event_records").fetchall()
        updates: list[tuple[str, str, int]] = []
        for stream_id, timestamp, payload in rows:
            normalized = _normalized_timestamp(timestamp, naive_timezone=self.event_timezone)
            if normalized is None or normalized == timestamp:
                continue
            serialized = str(payload)
            try:
                event = json.loads(serialized)
                if isinstance(event, dict):
                    event["timestamp"] = normalized
                    serialized = json.dumps(event, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                pass
            updates.append((normalized, serialized, int(stream_id)))
        if updates:
            connection.executemany(
                "UPDATE event_records SET timestamp = ?, payload = ? WHERE stream_id = ?",
                updates,
            )

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        return str(value) if value is not None and str(value) else None

    @staticmethod
    def _legacy_event_id(payload: dict[str, Any]) -> str:
        # Legacy logs may lack event_id. Stable hash avoids accidentally merging
        # unrelated records while retaining deterministic import behavior.
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return "legacy-" + hashlib.sha256(encoded).hexdigest()[:24]
