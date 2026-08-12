"""Atomic JSON camera repository with optimistic revision control."""
from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path

from .models import CAMERA_SCHEMA_VERSION, CameraConfig


class RevisionConflict(Exception):
    pass


class CameraAlreadyExists(Exception):
    pass


class CameraLimitReached(Exception):
    pass


class CameraRepository:
    MAX_CAMERAS = 16
    def __init__(self, directory: Path):
        self.directory = directory
        self.path = directory / "cameras.json"
        self.backup_path = directory / "cameras.json.bak"
        self._lock = threading.Lock()

    def _read(self) -> dict:
        if not self.path.exists():
            return {"schema_version": CAMERA_SCHEMA_VERSION, "revision": 0, "cameras": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("schema_version") != CAMERA_SCHEMA_VERSION or not isinstance(data.get("cameras"), list):
                raise ValueError("unsupported camera repository schema")
            return data
        except (json.JSONDecodeError, OSError, AttributeError) as exc:
            raise ValueError("camera repository is unreadable") from exc

    def list(self) -> tuple[int, list[CameraConfig]]:
        data = self._read()
        return int(data["revision"]), [CameraConfig(**item) for item in data["cameras"]]

    def get(self, camera_id: str) -> CameraConfig | None:
        _, cameras = self.list()
        return next((c for c in cameras if c.id == camera_id), None)

    def save(self, camera: CameraConfig, expected_revision: int | None) -> tuple[int, CameraConfig | None]:
        with self._lock:
            data = self._read()
            if expected_revision is not None and expected_revision != data["revision"]:
                raise RevisionConflict(f"expected revision {expected_revision}, current revision {data['revision']}")
            old = next((CameraConfig(**c) for c in data["cameras"] if c["id"] == camera.id), None)
            data["cameras"] = [c for c in data["cameras"] if c["id"] != camera.id] + [camera.model_dump()]
            data["cameras"].sort(key=lambda c: c["id"])
            return self._write(data), old

    def update_profile(
        self,
        camera_id: str,
        profile: str,
        enabled_events: list[str],
        expected_revision: int | None = None,
    ) -> tuple[int, CameraConfig]:
        """Atomically merge policy fields without overwriting concurrent camera edits."""
        with self._lock:
            data = self._read()
            if expected_revision is not None and expected_revision != data["revision"]:
                raise RevisionConflict(
                    f"expected revision {expected_revision}, current revision {data['revision']}"
                )
            index = next(
                (i for i, item in enumerate(data["cameras"]) if item["id"] == camera_id),
                None,
            )
            if index is None:
                raise KeyError(camera_id)
            current = CameraConfig(**data["cameras"][index])
            updated = current.model_copy(update={
                "profile": profile,
                "enabled_events": list(enabled_events),
            })
            data["cameras"][index] = updated.model_dump()
            return self._write(data), updated

    def create(
        self,
        camera: CameraConfig,
        expected_revision: int | None,
        occupied_ids: set[str] | None = None,
    ) -> int:
        """Atomically create a managed camera without overwriting an existing record."""
        with self._lock:
            data = self._read()
            if expected_revision is not None and expected_revision != data["revision"]:
                raise RevisionConflict(f"expected revision {expected_revision}, current revision {data['revision']}")
            if any(item["id"] == camera.id for item in data["cameras"]):
                raise CameraAlreadyExists(camera.id)
            total_ids = {
                *(occupied_ids or set()),
                *(str(item["id"]) for item in data["cameras"]),
                camera.id,
            }
            if len(total_ids) > self.MAX_CAMERAS:
                raise CameraLimitReached(f"total camera limit reached ({self.MAX_CAMERAS})")
            data["cameras"].append(camera.model_dump())
            data["cameras"].sort(key=lambda item: item["id"])
            return self._write(data)

    def delete(self, camera_id: str, expected_revision: int | None) -> tuple[int, CameraConfig]:
        with self._lock:
            data = self._read()
            if expected_revision is not None and expected_revision != data["revision"]:
                raise RevisionConflict(f"expected revision {expected_revision}, current revision {data['revision']}")
            old = next((CameraConfig(**c) for c in data["cameras"] if c["id"] == camera_id), None)
            if old is None:
                raise KeyError(camera_id)
            data["cameras"] = [c for c in data["cameras"] if c["id"] != camera_id]
            return self._write(data), old

    def _write(self, data: dict) -> int:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        if self.path.exists():
            shutil.copy2(self.path, self.backup_path)
            os.chmod(self.backup_path, 0o600)
        data["revision"] = int(data["revision"]) + 1
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)
        return data["revision"]
