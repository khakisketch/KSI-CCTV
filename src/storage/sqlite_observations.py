"""SQLite-backed observation buckets with versioned migrations and retention."""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.detection.yolo_detector import Detection
from src.events.observations import ObservationBucket, ObservationStore

from .schema import UnsupportedSchemaVersionError

_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS observation_buckets (
            camera_id TEXT NOT NULL,
            bucket_start TEXT NOT NULL,
            samples INTEGER NOT NULL,
            latest_counts TEXT NOT NULL,
            max_counts TEXT NOT NULL,
            model_counts TEXT NOT NULL,
            zone_max_counts TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (camera_id, bucket_start)
        );
        CREATE INDEX IF NOT EXISTS idx_observation_bucket_start
            ON observation_buckets(bucket_start DESC);
        """,
    ),
)


class SQLiteObservationStore(ObservationStore):
    """Persist ObservationStore buckets while preserving its bounded in-memory API."""

    def __init__(
        self,
        path: str | Path,
        max_buckets_per_camera: int = 1440,
        retention_days: int = 30,
        prune_interval_sec: float = 21600.0,
    ):
        super().__init__(max_buckets_per_camera=max_buckets_per_camera)
        self.path = Path(path)
        self.retention_days = max(1, int(retention_days))
        self.prune_interval_sec = max(1.0, float(prune_interval_sec))
        self._db_lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        self._loaded_cameras: set[str] = set()
        self._last_prune = 0.0

    def record(
        self,
        camera_id: str,
        timestamp: datetime,
        detections_by_model: dict[str, list[Detection]],
        zones: dict[str, Any] | None = None,
    ) -> ObservationBucket:
        with self._lock:
            self._load_camera(camera_id)
            camera_buckets = self._buckets[camera_id]
            bucket_start = timestamp.replace(second=0, microsecond=0)
            updates_current = bool(
                camera_buckets and camera_buckets[-1].bucket_start == bucket_start
            )
            previous_bucket = copy.deepcopy(camera_buckets[-1]) if updates_current else None
            evicted_bucket = (
                camera_buckets[0]
                if not updates_current and len(camera_buckets) == camera_buckets.maxlen
                else None
            )
            try:
                bucket = super().record(camera_id, timestamp, detections_by_model, zones)
                payload = bucket.to_dict()
                connection = self._connect()
                with self._db_lock, connection:
                    connection.execute(
                        """
                        INSERT INTO observation_buckets (
                            camera_id, bucket_start, samples, latest_counts, max_counts,
                            model_counts, zone_max_counts, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(camera_id, bucket_start) DO UPDATE SET
                            samples=excluded.samples,
                            latest_counts=excluded.latest_counts,
                            max_counts=excluded.max_counts,
                            model_counts=excluded.model_counts,
                            zone_max_counts=excluded.zone_max_counts,
                            updated_at=excluded.updated_at
                        """,
                        (
                            camera_id,
                            payload["bucket_start"],
                            payload["samples"],
                            self._json(payload["latest_counts"]),
                            self._json(payload["max_counts"]),
                            self._json(payload["model_counts"]),
                            self._json(payload["zone_max_counts"]),
                            datetime.now().isoformat(),
                        ),
                    )
            except Exception:
                if updates_current and previous_bucket is not None:
                    camera_buckets[-1] = previous_bucket
                else:
                    camera_buckets.pop()
                    if evicted_bucket is not None:
                        camera_buckets.appendleft(evicted_bucket)
                raise
        self._prune_if_due()
        return bucket

    def query(self, camera_id: str | None = None, limit: int = 120) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 2000))
        connection = self._connect()
        sql = """
            SELECT camera_id, bucket_start, samples, latest_counts, max_counts,
                   model_counts, zone_max_counts
            FROM observation_buckets
        """
        params: tuple[Any, ...]
        if camera_id is None:
            sql += " ORDER BY bucket_start DESC LIMIT ?"
            params = (safe_limit,)
        else:
            sql += " WHERE camera_id = ? ORDER BY bucket_start DESC LIMIT ?"
            params = (camera_id, safe_limit)
        with self._db_lock:
            rows = connection.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def reset(self, camera_id: str | None = None) -> None:
        super().reset(camera_id)
        connection = self._connect()
        with self._db_lock, connection:
            if camera_id is None:
                connection.execute("DELETE FROM observation_buckets")
                self._loaded_cameras.clear()
            else:
                connection.execute(
                    "DELETE FROM observation_buckets WHERE camera_id = ?", (camera_id,)
                )
                self._loaded_cameras.discard(camera_id)

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now()) - timedelta(days=self.retention_days)
        connection = self._connect()
        with self._db_lock, connection:
            cursor = connection.execute(
                "DELETE FROM observation_buckets WHERE bucket_start < ?",
                (cutoff.isoformat(),),
            )
            deleted = max(0, int(cursor.rowcount))
        if deleted:
            with self._lock:
                self._buckets.clear()
                self._loaded_cameras.clear()
        return deleted

    def close(self) -> None:
        with self._db_lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _connect(self) -> sqlite3.Connection:
        with self._db_lock:
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
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                applied = {
                    int(row[0])
                    for row in connection.execute("SELECT version FROM schema_migrations")
                }
                newest_version = max(applied, default=0)
                supported_version = max(version for version, _ in _MIGRATIONS)
                if newest_version > supported_version:
                    raise UnsupportedSchemaVersionError(
                        "observation database", newest_version, supported_version
                    )
                for version, sql in _MIGRATIONS:
                    if version in applied:
                        continue
                    with connection:
                        connection.executescript(sql)
                        connection.execute(
                            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                            (version, datetime.now().isoformat()),
                        )
                os.chmod(self.path, 0o600)
            except Exception:
                # Do not cache a partly initialized connection after a failed migration.
                connection.close()
                raise
            self._connection = connection
            return connection

    def _load_camera(self, camera_id: str) -> None:
        with self._lock:
            if camera_id in self._loaded_cameras:
                return
        connection = self._connect()
        with self._db_lock:
            rows = connection.execute(
                """
                SELECT camera_id, bucket_start, samples, latest_counts, max_counts,
                       model_counts, zone_max_counts
                FROM observation_buckets
                WHERE camera_id = ?
                ORDER BY bucket_start DESC
                LIMIT ?
                """,
                (camera_id, self.max_buckets_per_camera),
            ).fetchall()
        buckets: deque[ObservationBucket] = deque(maxlen=self.max_buckets_per_camera)
        for row in reversed(rows):
            item = self._row_to_dict(row)
            buckets.append(ObservationBucket(
                camera_id=item["camera_id"],
                bucket_start=datetime.fromisoformat(item["bucket_start"]),
                samples=item["samples"],
                latest_counts=item["latest_counts"],
                max_counts=item["max_counts"],
                model_counts=item["model_counts"],
                zone_max_counts=item["zone_max_counts"],
            ))
        with self._lock:
            if camera_id not in self._loaded_cameras:
                self._buckets[camera_id] = buckets
                self._loaded_cameras.add(camera_id)

    def _prune_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_prune < self.prune_interval_sec:
            return
        self.prune()
        self._last_prune = now

    @staticmethod
    def _json(value: dict[str, int]) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _row_to_dict(row: sqlite3.Row | tuple) -> dict[str, Any]:
        return {
            "camera_id": str(row[0]),
            "bucket_start": str(row[1]),
            "samples": int(row[2]),
            "latest_counts": json.loads(row[3]),
            "max_counts": json.loads(row[4]),
            "model_counts": json.loads(row[5]),
            "zone_max_counts": json.loads(row[6]),
        }
