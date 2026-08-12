"""Small Fernet-backed credential vault with owner-only files."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

_UNCHANGED = object()


class CredentialStore:
    def __init__(self, directory: Path):
        self.directory = directory
        self.key_path = directory / "credentials.key"
        self.data_path = directory / "credentials.enc"
        self._lock = threading.Lock()

    def _fernet(self) -> Fernet:
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        if not self.key_path.exists():
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(Fernet.generate_key())
                f.flush()
                os.fsync(f.fileno())
        os.chmod(self.key_path, 0o600)
        return Fernet(self.key_path.read_bytes())

    def _read(self) -> dict[str, Any]:
        if not self.data_path.exists():
            return {}
        try:
            return json.loads(self._fernet().decrypt(self.data_path.read_bytes()).decode("utf-8"))
        except Exception as exc:
            raise ValueError("credential store cannot be decrypted") from exc

    def put(
        self,
        ref: str,
        username: str | None,
        password: str | None,
        target_scope: str | None | object = _UNCHANGED,
    ) -> None:
        if password is None and username is None and target_scope is _UNCHANGED:
            return
        with self._lock:
            data = self._read()
            current = data.get(ref, {}) if isinstance(data.get(ref), dict) else {}
            updated = {
                "username": username if username is not None else current.get("username", ""),
                "password": password if password is not None else current.get("password", ""),
            }
            scope = current.get("target_scope") if target_scope is _UNCHANGED else target_scope
            if scope is not None:
                updated["target_scope"] = scope
            data[ref] = updated
            self._write(data)

    def bind_target(self, ref: str | None, target_scope: str) -> None:
        if not ref:
            return
        with self._lock:
            data = self._read()
            current = data.get(ref)
            if not isinstance(current, dict) or current.get("target_scope") == target_scope:
                return
            data[ref] = {**current, "target_scope": target_scope}
            self._write(data)

    @staticmethod
    def target_scope(addresses: list[str], port: int) -> str:
        return f"{port}|{','.join(sorted(set(addresses)))}"

    def _write(self, data: dict[str, Any]) -> None:
        encrypted = self._fernet().encrypt(json.dumps(data, separators=(",", ":")).encode("utf-8"))
        tmp = self.data_path.with_name(f".{self.data_path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(encrypted)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.data_path)
        os.chmod(self.data_path, 0o600)

    def get(self, ref: str | None) -> dict[str, str] | None:
        if not ref:
            return None
        with self._lock:
            value = self._read().get(ref)
        return value if isinstance(value, dict) else None

    def delete(self, ref: str | None) -> None:
        if not ref:
            return
        with self._lock:
            data = self._read()
            if ref in data:
                del data[ref]
                self._write(data)
