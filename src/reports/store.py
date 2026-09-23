"""P3-T2: SQLite 보고서 저장소 (기존 storage 스키마와 독립된 reports DB).

보존기간 기본 90일. config 키 이름(``reports.retention_days``)만 상수로 정의하고
값 변경은 T3 config 계층에서 다룬다 — 본 모듈은 config 파일을 읽지 않는다.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_RETENTION_DAYS = 90
RETENTION_CONFIG_KEY = "reports.retention_days"
VALID_PERIODS = frozenset({"daily", "weekly"})
_REPORT_SCHEMA_VERSION = 1

_REQUIRED_FIELDS = ("period", "since", "until", "model", "markdown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_now(now: datetime | None) -> datetime:
    """prune 기준시각을 UTC aware로 정규화한다.

    naive는 UTC로 간주하며 raise하지 않는다 (R3). 그대로 문자열 비교하면
    오프셋 표기가 다른 행과 순서가 뒤집혀 과삭제/미삭제가 발생하므로,
    장기적으로 저장되는 ``created_at`` 형식(+00:00)에 맞춘다.
    """
    current = now if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


class ReportStore:
    """보고서 Markdown 레코드 저장소 (id·period·since/until·model·markdown·created_at)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def save(self, report: dict[str, Any]) -> int:
        """보고서를 저장하고 정수 id를 반환한다. period는 daily/weekly만 허용."""
        period = str(report.get("period", ""))
        if period not in VALID_PERIODS:
            raise ValueError("report period must be one of daily/weekly")
        values: dict[str, str] = {}
        for field in _REQUIRED_FIELDS[1:]:
            text = str(report.get(field, ""))
            if not text.strip():
                raise ValueError(f"report {field} must be non-empty")
            values[field] = text
        created_at = _now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO reports(period, since, until, model, markdown, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (period, values["since"], values["until"], values["model"], values["markdown"], created_at),
            )
            report_id = cursor.lastrowid
        if report_id is None:
            raise RuntimeError("SQLite did not return a report id")
        return int(report_id)

    def get(self, report_id: int) -> dict[str, Any] | None:
        """id로 보고서를 조회한다. 없으면 None."""
        with self._lock:
            row = self._connect().execute(
                "SELECT id, period, since, until, model, markdown, created_at "
                "FROM reports WHERE id = ?",
                (report_id,),
            ).fetchone()
        return self._row_to_dict(row) if row is not None else None

    def list(self, limit: int = 100, period: str | None = None) -> list[dict[str, Any]]:
        """최신 보고서부터 반환한다. period 지정 시 해당 주기만(SQL WHERE, 절단 전 필터)."""
        safe_limit = max(1, min(int(limit), 1000))
        if period is not None and period not in VALID_PERIODS:
            raise ValueError("report period must be one of daily/weekly")
        with self._lock:
            if period is None:
                rows = self._connect().execute(
                    "SELECT id, period, since, until, model, markdown, created_at "
                    "FROM reports ORDER BY id DESC LIMIT ?",
                    (safe_limit,),
                ).fetchall()
            else:
                rows = self._connect().execute(
                    "SELECT id, period, since, until, model, markdown, created_at "
                    "FROM reports WHERE period = ? ORDER BY id DESC LIMIT ?",
                    (period, safe_limit),
                ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def prune(
        self,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        now: datetime | None = None,
    ) -> int:
        """보존기간을 초과한 보고서를 삭제하고 삭제 건수를 반환한다.

        naive ``now``는 UTC로 간주한다 (raise하지 않음).
        """
        cutoff = _coerce_now(now) - timedelta(days=max(1, int(retention_days)))
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM reports WHERE created_at < ?",
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
                    "CREATE TABLE IF NOT EXISTS report_schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                applied = {
                    int(row[0]) for row in connection.execute("SELECT version FROM report_schema_migrations")
                }
                if max(applied, default=0) > _REPORT_SCHEMA_VERSION:
                    raise RuntimeError("report database was written by newer code")
                with connection:
                    connection.executescript(
                        """
                    CREATE TABLE IF NOT EXISTS reports (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        period TEXT NOT NULL,
                        since TEXT NOT NULL,
                        until TEXT NOT NULL,
                        model TEXT NOT NULL,
                        markdown TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_reports_created
                        ON reports(created_at DESC);
                        """
                    )
                    if _REPORT_SCHEMA_VERSION not in applied:
                        connection.execute(
                            "INSERT INTO report_schema_migrations(version, applied_at) VALUES (?, ?)",
                            (_REPORT_SCHEMA_VERSION, _now()),
                        )
                os.chmod(self.path, 0o600)
            except Exception:
                connection.close()
                raise
            self._connection = connection
            return connection

    @staticmethod
    def _row_to_dict(row: Any) -> dict[str, Any]:
        return {
            "id": int(row[0]),
            "period": str(row[1]),
            "since": str(row[2]),
            "until": str(row[3]),
            "model": str(row[4]),
            "markdown": str(row[5]),
            "created_at": str(row[6]),
        }
