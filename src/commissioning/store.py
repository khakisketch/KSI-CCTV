"""Atomic, non-secret commissioning progress store."""
from __future__ import annotations

import json
import os
import shutil
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class CommissioningConflict(Exception):
    """Raised when an installer edits stale commissioning state."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_handoff() -> dict[str, Any]:
    return {"customer_name": "", "notes": "", "accepted_at": None}


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "revision": 0,
        "site": {
            "name": "",
            "location": "",
            "timezone": "Asia/Seoul",
            "installer": "",
            "expected_camera_count": 0,
        },
        "camera_checks": {},
        "completed_at": None,
        "handoff": _empty_handoff(),
        "updated_at": None,
    }


class CommissioningStore:
    """Store installer progress without credentials or private camera URLs."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "commissioning.json"
        self.backup_path = directory / "commissioning.json.bak"
        self._lock = threading.Lock()

    def read(self) -> dict[str, Any]:
        if not self.path.exists():
            return _empty_state()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("commissioning state is unreadable") from exc
        if data.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("unsupported commissioning schema")
        state = _empty_state()
        state.update(data)
        state["site"] = {**_empty_state()["site"], **(data.get("site") or {})}
        state["camera_checks"] = data.get("camera_checks") or {}
        state["handoff"] = {**_empty_handoff(), **(data.get("handoff") or {})}
        return state

    def update_site(
        self,
        site: dict[str, Any],
        expected_revision: int | None,
    ) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            state.update({
                "site": {**state["site"], **site},
                "completed_at": None,
                "handoff": _empty_handoff(),
            })

        return self._update(expected_revision, mutate)

    def update_camera(
        self,
        camera_id: str,
        checks: dict[str, Any],
        expected_revision: int | None,
    ) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            current = state["camera_checks"].get(camera_id, {})
            state["camera_checks"][camera_id] = {
                **current,
                **checks,
                "updated_at": _now(),
            }
            state["completed_at"] = None
            state["handoff"] = _empty_handoff()

        return self._update(expected_revision, mutate)

    def mark_completed(self, expected_revision: int | None) -> dict[str, Any]:
        return self._update(
            expected_revision,
            lambda state: state.update({
                "completed_at": _now(),
                "handoff": _empty_handoff(),
            }),
        )

    def record_handoff(
        self,
        customer_name: str,
        notes: str,
        expected_revision: int | None,
    ) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            if not state.get("completed_at"):
                raise ValueError("commissioning must be completed before handoff")
            state["handoff"] = {
                "customer_name": customer_name,
                "notes": notes,
                "accepted_at": _now(),
            }

        return self._update(expected_revision, mutate)

    def reopen(self, expected_revision: int | None) -> dict[str, Any]:
        return self._update(
            expected_revision,
            lambda state: state.update({
                "completed_at": None,
                "handoff": _empty_handoff(),
            }),
        )

    def invalidate_completion(self) -> dict[str, Any]:
        """Invalidate an accepted baseline after a successful system configuration change."""
        with self._lock:
            state = self.read()
            handoff = state.get("handoff") or {}
            if not state.get("completed_at") and not handoff.get("accepted_at"):
                return deepcopy(state)
            state["completed_at"] = None
            state["handoff"] = _empty_handoff()
            state["revision"] = int(state["revision"]) + 1
            state["updated_at"] = _now()
            self._write(state)
            return deepcopy(state)

    def _update(self, expected_revision: int | None, mutate: Any) -> dict[str, Any]:
        with self._lock:
            state = self.read()
            if expected_revision is not None and expected_revision != state["revision"]:
                raise CommissioningConflict(
                    f"expected revision {expected_revision}, current revision {state['revision']}"
                )
            mutate(state)
            state["revision"] = int(state["revision"]) + 1
            state["updated_at"] = _now()
            self._write(state)
            return deepcopy(state)

    def _write(self, state: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        if self.path.exists():
            shutil.copy2(self.path, self.backup_path)
            os.chmod(self.backup_path, 0o600)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
