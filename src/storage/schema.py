"""Shared schema compatibility errors for durable SQLite stores."""
from __future__ import annotations


class UnsupportedSchemaVersionError(RuntimeError):
    """Raised when a database was migrated by a newer application version."""

    def __init__(self, store: str, found_version: int, supported_version: int) -> None:
        self.store = store
        self.found_version = found_version
        self.supported_version = supported_version
        super().__init__(
            f"{store} schema version {found_version} is newer than supported version "
            f"{supported_version}"
        )
