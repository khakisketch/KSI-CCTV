"""Versioned, non-secret camera schema."""
from __future__ import annotations

import re
import secrets
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator, model_validator

from src.utils.config import mask_url

CAMERA_SCHEMA_VERSION = 1
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ZonePoint(BaseModel):
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)


class CameraZone(BaseModel):
    id: str = Field(default="restricted", min_length=1, max_length=64)
    type: Literal["polygon", "line"] = "polygon"
    points: list[ZonePoint] = Field(min_length=2, max_length=64)
    direction: Literal["any", "positive", "negative"] = "any"

    @field_validator("id")
    @classmethod
    def valid_zone_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("zone id must contain only letters, numbers, underscores, or hyphens")
        return value

    @model_validator(mode="after")
    def valid_point_count(self) -> CameraZone:
        if self.type == "polygon" and len(self.points) < 3:
            raise ValueError("polygon zones require at least 3 points")
        if self.type == "line" and len(self.points) != 2:
            raise ValueError("line zones require exactly 2 points")
        return self


class CameraInput(BaseModel):
    """Installer input. Password is accepted only to move it into CredentialStore."""

    schema_version: Literal[CAMERA_SCHEMA_VERSION] = CAMERA_SCHEMA_VERSION
    id: str | None = None
    name: str = Field(min_length=1, max_length=128)
    location: str = Field(default="", max_length=256)
    rtsp_url: str = Field(min_length=1, max_length=2048)
    enabled: bool = True
    # OpenCV's FFMPEG backend cannot select UDP safely per capture instance.
    # Do not expose a transport choice that the runtime would silently ignore.
    transport: Literal["tcp"] = "tcp"
    fps: int = Field(default=10, ge=1, le=60)
    resolution: tuple[int, int] | None = None
    zone: str | None = Field(default=None, max_length=128)
    profile: str | None = Field(default=None, max_length=128)
    enabled_events: list[str] = Field(default_factory=list, max_length=128)
    zones: list[CameraZone] = Field(default_factory=list, max_length=32)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=1024)
    clear_credentials: bool = False

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _ID.fullmatch(value):
            raise ValueError("id must contain only letters, numbers, underscores, or hyphens")
        return value

    @field_validator("rtsp_url")
    @classmethod
    def valid_rtsp(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
            raise ValueError("rtsp_url must use rtsp:// or rtsps://")
        if parsed.username or parsed.password:
            raise ValueError("RTSP credentials must be supplied in username/password fields")
        if parsed.query or parsed.fragment:
            raise ValueError("RTSP query and fragment tokens are not supported; use the credential fields")
        return value

    @field_validator("enabled_events")
    @classmethod
    def valid_events(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for event in value:
            item = event.strip()
            if not item or len(item) > 128 or item in cleaned:
                continue
            cleaned.append(item)
        return cleaned

    @model_validator(mode="after")
    def valid_resolution(self) -> CameraInput:
        if self.clear_credentials and (self.username is not None or self.password is not None):
            raise ValueError("clear_credentials cannot be combined with username or password")
        if self.resolution is not None:
            width, height = self.resolution
            if not 160 <= width <= 7680 or not 120 <= height <= 4320:
                raise ValueError("resolution is outside supported bounds")
        return self


class CameraConfig(BaseModel):
    """Persisted camera record. Credentials are referenced, never embedded."""

    schema_version: Literal[CAMERA_SCHEMA_VERSION] = CAMERA_SCHEMA_VERSION
    id: str
    name: str
    location: str = ""
    rtsp_url: str
    enabled: bool = True
    transport: Literal["tcp"] = "tcp"
    fps: int = 10
    resolution: tuple[int, int] | None = None
    zone: str | None = None
    profile: str | None = None
    enabled_events: list[str] = Field(default_factory=list)
    zones: list[CameraZone] = Field(default_factory=list)
    credential_ref: str | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError(
                "id must contain only letters, numbers, underscores, or hyphens"
            )
        return value

    @classmethod
    def from_input(
        cls,
        item: CameraInput,
        credential_ref: str | None = None,
        camera_id: str | None = None,
    ) -> CameraConfig:
        resolved_id = camera_id or item.id or f"cam_{secrets.token_hex(4)}"
        return cls(
            **item.model_dump(exclude={"id", "username", "password", "clear_credentials"}),
            id=resolved_id,
            credential_ref=credential_ref,
        )

    def public_dict(self) -> dict:
        data = self.model_dump(exclude={"credential_ref"})
        data["credential_configured"] = bool(self.credential_ref)
        data["managed"] = True
        data["detection_type"] = self.profile
        # Managed cameras may have legacy URL userinfo even though new input
        # validation rejects it. Never disclose credentials through API views.
        data["stream_url"] = mask_url(self.rtsp_url)
        return data
