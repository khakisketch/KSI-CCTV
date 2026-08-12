"""Installer-facing camera configuration, validation, and diagnostics."""

from .credentials import CredentialStore
from .mediamtx import MediaMTXClient, MediaMTXError
from .models import CAMERA_SCHEMA_VERSION, CameraConfig, CameraInput, CameraZone, ZonePoint
from .onvif import OnvifAdapter, OnvifClient, WsDiscoveryClient
from .plan import ExecutionPlan, compile_plan
from .probe import RtspProbe
from .repository import CameraAlreadyExists, CameraLimitReached, CameraRepository, RevisionConflict

__all__ = [
    "CAMERA_SCHEMA_VERSION", "CameraConfig", "CameraInput", "CameraRepository", "CameraZone",
    "CredentialStore", "ExecutionPlan", "OnvifAdapter", "OnvifClient", "RevisionConflict",
    "CameraAlreadyExists", "CameraLimitReached", "MediaMTXClient", "MediaMTXError", "RtspProbe",
    "WsDiscoveryClient", "ZonePoint", "compile_plan",
]
