"""Ultralytics ByteTrack adapter for existing KSI Detection objects."""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import numpy as np

from src.detection.yolo_detector import Detection
from src.tracking.models import TrackPoint, TrackSnapshot


class _Results:
    """Minimal Ultralytics Results-compatible detection container."""

    def __init__(self, xyxy: np.ndarray, confidence: np.ndarray, classes: np.ndarray):
        self.xyxy = xyxy.astype(np.float32, copy=False)
        self.conf = confidence.astype(np.float32, copy=False)
        self.cls = classes.astype(np.float32, copy=False)

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, index) -> _Results:
        return _Results(self.xyxy[index], self.conf[index], self.cls[index])

    @property
    def xywh(self) -> np.ndarray:
        if not len(self):
            return np.empty((0, 4), dtype=np.float32)
        values = self.xyxy.copy()
        values[:, 0] = (self.xyxy[:, 0] + self.xyxy[:, 2]) / 2
        values[:, 1] = (self.xyxy[:, 1] + self.xyxy[:, 3]) / 2
        values[:, 2] = self.xyxy[:, 2] - self.xyxy[:, 0]
        values[:, 3] = self.xyxy[:, 3] - self.xyxy[:, 1]
        return values


class ByteTrackManager:
    """Maintain an independent BYTETracker and bounded trajectory per camera."""

    _global_tracker_lock = threading.RLock()

    def __init__(
        self,
        track_high_thresh: float = 0.35,
        track_low_thresh: float = 0.10,
        new_track_thresh: float = 0.40,
        track_buffer: int = 30,
        match_thresh: float = 0.80,
        trajectory_size: int = 120,
        stale_history_sec: float = 300.0,
    ):
        self.args = SimpleNamespace(
            track_high_thresh=float(track_high_thresh),
            track_low_thresh=float(track_low_thresh),
            new_track_thresh=float(new_track_thresh),
            track_buffer=int(track_buffer),
            match_thresh=float(match_thresh),
            fuse_score=True,
        )
        self.trajectory_size = max(2, int(trajectory_size))
        self.stale_history_sec = max(1.0, float(stale_history_sec))
        self._trackers: dict[str, object] = {}
        self._history: dict[tuple[str, int], deque[TrackPoint]] = {}
        self._first_seen: dict[tuple[str, int], datetime] = {}
        self._history_last_seen: dict[tuple[str, int], datetime] = {}
        self._class_names: dict[tuple[str, int], str] = {}
        self._generations: dict[str, int] = {}
        self._stats: dict[str, dict[str, float | int | str | None]] = {}
        self._lock = threading.RLock()

    def update(
        self,
        camera_id: str,
        detections: list[Detection],
        timestamp: datetime,
    ) -> list[TrackSnapshot]:
        tracker, generation = self._tracker(camera_id)
        results = self._to_results(detections)
        with self._lock:
            for item in detections:
                self._class_names[(camera_id, item.class_id)] = item.class_name
        started = time.perf_counter()
        try:
            with self._global_tracker_lock:
                tracked = tracker.update(results)
        except Exception:
            with self._lock:
                stats = self._stats.setdefault(camera_id, self._empty_stats())
                stats["errors"] = int(stats["errors"] or 0) + 1
            raise
        latency_ms = (time.perf_counter() - started) * 1000.0

        snapshots: list[TrackSnapshot] = []
        with self._lock:
            if (
                self._generations.get(camera_id, 0) != generation
                or self._trackers.get(camera_id) is not tracker
            ):
                return []
            stats = self._stats.setdefault(camera_id, self._empty_stats())
            stats["updates"] = int(stats["updates"] or 0) + 1
            stats["detections"] = int(stats["detections"] or 0) + len(detections)
            stats["last_latency_ms"] = latency_ms
            stats["total_latency_ms"] = float(stats["total_latency_ms"] or 0.0) + latency_ms
            stats["last_update"] = timestamp.isoformat()
            for row in tracked:
                if len(row) < 7:
                    continue
                x1, y1, x2, y2 = (int(round(value)) for value in row[:4])
                track_id = int(row[4])
                confidence = float(row[5])
                class_id = int(row[6])
                class_name = self._class_names.get((camera_id, class_id))
                if class_name is None:
                    continue
                key = (camera_id, track_id)
                history = self._history.setdefault(
                    key, deque(maxlen=self.trajectory_size)
                )
                history.append(TrackPoint((x1 + x2) / 2, (y1 + y2) / 2, timestamp))
                first_seen = self._first_seen.setdefault(key, timestamp)
                self._history_last_seen[key] = timestamp
                snapshots.append(TrackSnapshot(
                    camera_id=camera_id,
                    track_id=track_id,
                    class_name=class_name,
                    bbox=(x1, y1, x2, y2),
                    confidence=confidence,
                    first_seen=first_seen,
                    last_seen=timestamp,
                    trajectory=tuple(history),
                ))
            self._prune_stale_history(camera_id, timestamp)
            return snapshots

    def reset(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id is None:
                camera_ids = {
                    *self._trackers.keys(),
                    *(key[0] for key in self._history),
                }
                for existing_id in camera_ids:
                    self._generations[existing_id] = (
                        self._generations.get(existing_id, 0) + 1
                    )
                self._trackers.clear()
                self._history.clear()
                self._first_seen.clear()
                self._history_last_seen.clear()
                self._class_names.clear()
                self._stats.clear()
                return
            self._generations[camera_id] = self._generations.get(camera_id, 0) + 1
            self._trackers.pop(camera_id, None)
            for key in list(self._history):
                if key[0] == camera_id:
                    self._history.pop(key, None)
                    self._first_seen.pop(key, None)
                    self._history_last_seen.pop(key, None)
            for key in list(self._class_names):
                if key[0] == camera_id:
                    self._class_names.pop(key, None)
            self._stats.pop(camera_id, None)

    def stats(self, camera_id: str) -> dict[str, float | int | str | None]:
        with self._lock:
            values = dict(self._stats.get(camera_id, self._empty_stats()))
            updates = int(values["updates"] or 0)
            total = float(values.pop("total_latency_ms", 0.0) or 0.0)
            values["average_latency_ms"] = total / updates if updates else 0.0
            return values

    @staticmethod
    def _empty_stats() -> dict[str, float | int | str | None]:
        return {
            "updates": 0,
            "detections": 0,
            "errors": 0,
            "last_latency_ms": 0.0,
            "total_latency_ms": 0.0,
            "last_update": None,
        }

    def _tracker(self, camera_id: str) -> tuple[Any, int]:
        with self._lock:
            existing = self._trackers.get(camera_id)
            if existing is not None:
                return existing, self._generations.get(camera_id, 0)
            # BYTETracker resets a global ID counter in its constructor. Preserve
            # the counter so dynamically adding a camera cannot duplicate IDs.
            with self._global_tracker_lock:
                from ultralytics.trackers.basetrack import BaseTrack
                from ultralytics.trackers.byte_tracker import BYTETracker

                current_count = BaseTrack._count
                tracker = BYTETracker(self.args)
                BaseTrack._count = current_count
            self._trackers[camera_id] = tracker
            return tracker, self._generations.get(camera_id, 0)

    def _prune_stale_history(self, camera_id: str, timestamp: datetime) -> None:
        stale = [
            key
            for key, last_seen in self._history_last_seen.items()
            if key[0] == camera_id
            and timestamp >= last_seen
            and (timestamp - last_seen).total_seconds() > self.stale_history_sec
        ]
        for key in stale:
            self._history.pop(key, None)
            self._first_seen.pop(key, None)
            self._history_last_seen.pop(key, None)

    @staticmethod
    def _to_results(detections: list[Detection]) -> _Results:
        if not detections:
            return _Results(
                np.empty((0, 4), dtype=np.float32),
                np.empty((0,), dtype=np.float32),
                np.empty((0,), dtype=np.float32),
            )
        return _Results(
            np.asarray([item.bbox for item in detections], dtype=np.float32),
            np.asarray([item.confidence for item in detections], dtype=np.float32),
            np.asarray([item.class_id for item in detections], dtype=np.float32),
        )
