"""Camera ingest health state transitions, independent from object detection."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any


class CameraHealthState(str, Enum):
    INITIALIZING = "initializing"
    RECEIVING = "receiving"
    STALE = "stale"
    DISCONNECTED = "disconnected"


@dataclass(frozen=True)
class CameraHealthTransition:
    camera_id: str
    previous: CameraHealthState
    current: CameraHealthState
    event_type: str | None = None


@dataclass
class _CameraState:
    state: CameraHealthState
    first_seen_at: float


class CameraHealthMonitor:
    """Convert capture telemetry into deduplicated disconnect/recovery transitions."""

    def __init__(self, stale_after_sec: float = 10.0, disconnect_after_sec: float = 30.0):
        self.stale_after_sec = max(0.1, float(stale_after_sec))
        self.disconnect_after_sec = max(self.stale_after_sec, float(disconnect_after_sec))
        self._states: dict[str, _CameraState] = {}
        self._lock = threading.RLock()

    def update(
        self,
        camera_id: str,
        status: dict[str, Any],
        now: float | None = None,
    ) -> CameraHealthTransition | None:
        current_time = time.monotonic() if now is None else float(now)
        with self._lock:
            stored = self._states.get(camera_id)
            if stored is None:
                stored = _CameraState(CameraHealthState.INITIALIZING, current_time)
                self._states[camera_id] = stored

            next_state = self._classify(status, current_time - stored.first_seen_at)
            previous = stored.state
            if next_state == previous:
                return None

            stored.state = next_state
            event_type = None
            if next_state == CameraHealthState.DISCONNECTED:
                event_type = "camera_disconnect"
            elif previous == CameraHealthState.DISCONNECTED and next_state == CameraHealthState.RECEIVING:
                event_type = "camera_recovered"
            return CameraHealthTransition(camera_id, previous, next_state, event_type)

    def state(self, camera_id: str) -> CameraHealthState:
        with self._lock:
            stored = self._states.get(camera_id)
            return stored.state if stored else CameraHealthState.INITIALIZING

    def reset(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id is None:
                self._states.clear()
            else:
                self._states.pop(camera_id, None)

    def _classify(self, status: dict[str, Any], startup_elapsed: float) -> CameraHealthState:
        frame_count = int(status.get("frame_count") or 0)
        elapsed_value = status.get("seconds_since_last_frame")
        elapsed = float(elapsed_value) if isinstance(elapsed_value, (int, float)) else None

        if frame_count <= 0 or elapsed is None:
            return (
                CameraHealthState.DISCONNECTED
                if startup_elapsed >= self.disconnect_after_sec
                else CameraHealthState.INITIALIZING
            )
        if elapsed >= self.disconnect_after_sec:
            return CameraHealthState.DISCONNECTED
        if elapsed >= self.stale_after_sec or bool(status.get("is_stale", False)):
            return CameraHealthState.STALE
        return CameraHealthState.RECEIVING
