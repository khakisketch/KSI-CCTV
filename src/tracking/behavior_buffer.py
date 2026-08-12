"""Bounded, camera-isolated pose sequences for action classification."""

from __future__ import annotations

import threading
from collections import deque

from src.detection.pose_models import PoseObservation


class BehaviorBuffer:
    """Keep keypoints only, keyed by camera and track, with strict timestamp ordering."""

    def __init__(
        self,
        sequence_length: int,
        *,
        stale_after_sec: float = 10.0,
        max_tracks_per_camera: int = 100,
    ) -> None:
        if sequence_length <= 0 or stale_after_sec <= 0 or max_tracks_per_camera <= 0:
            raise ValueError(
                "sequence_length, stale_after_sec, and max_tracks_per_camera must be positive"
            )
        self.sequence_length = int(sequence_length)
        self.stale_after_sec = float(stale_after_sec)
        self.max_tracks_per_camera = int(max_tracks_per_camera)
        self._buffers: dict[str, dict[int, deque[PoseObservation]]] = {}
        self._last_seen: dict[tuple[str, int], float] = {}
        self._counters = {"appended": 0, "rejected_out_of_order": 0, "evicted": 0, "pruned": 0}
        self._lock = threading.RLock()

    def append(self, camera_id: str, track_id: int, observation: PoseObservation) -> bool:
        """Append a newer observation; return false rather than corrupting sequence order."""
        if not camera_id:
            raise ValueError("camera_id must not be empty")
        with self._lock:
            track_id = int(track_id)
            key = (camera_id, track_id)
            previous = self._last_seen.get(key)
            if previous is not None and observation.timestamp <= previous:
                self._counters["rejected_out_of_order"] += 1
                return False
            tracks = self._buffers.setdefault(camera_id, {})
            if track_id not in tracks and len(tracks) >= self.max_tracks_per_camera:
                oldest = min(tracks, key=lambda candidate: self._last_seen[(camera_id, candidate)])
                self._remove(camera_id, oldest)
                self._counters["evicted"] += 1
            tracks.setdefault(track_id, deque(maxlen=self.sequence_length)).append(observation)
            self._last_seen[key] = observation.timestamp
            self._counters["appended"] += 1
            return True

    def sequence(self, camera_id: str, track_id: int) -> tuple[PoseObservation, ...] | None:
        with self._lock:
            values = self._buffers.get(camera_id, {}).get(int(track_id))
            if values is None or len(values) != self.sequence_length:
                return None
            return tuple(values)

    def prune_stale(self, now: float) -> int:
        with self._lock:
            stale = [
                key
                for key, last_seen in self._last_seen.items()
                if now >= last_seen and now - last_seen > self.stale_after_sec
            ]
            for camera_id, track_id in stale:
                self._remove(camera_id, track_id)
            self._counters["pruned"] += len(stale)
            return len(stale)

    def reset(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id is None:
                self._buffers.clear()
                self._last_seen.clear()
                return
            for track_id in list(self._buffers.get(camera_id, {})):
                self._remove(camera_id, track_id)

    def status(self, camera_id: str | None = None) -> dict[str, int]:
        with self._lock:
            cameras = [camera_id] if camera_id is not None else list(self._buffers)
            tracks = sum(len(self._buffers.get(camera, {})) for camera in cameras)
            observations = sum(
                len(values)
                for camera in cameras
                for values in self._buffers.get(camera, {}).values()
            )
            return {
                **self._counters,
                "cameras": len(self._buffers),
                "tracks": tracks,
                "observations": observations,
            }

    def _remove(self, camera_id: str, track_id: int) -> None:
        tracks = self._buffers.get(camera_id)
        if tracks is None:
            return
        tracks.pop(track_id, None)
        self._last_seen.pop((camera_id, track_id), None)
        if not tracks:
            self._buffers.pop(camera_id, None)
