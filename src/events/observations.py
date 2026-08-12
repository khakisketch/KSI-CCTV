"""Bounded metadata-only observation aggregation.

Observations are not incidents: they never carry frames, trigger alerts, or invoke VLM.
"""
from __future__ import annotations

import threading
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.detection.yolo_detector import Detection


@dataclass
class ObservationBucket:
    camera_id: str
    bucket_start: datetime
    samples: int = 0
    latest_counts: dict[str, int] = field(default_factory=dict)
    max_counts: dict[str, int] = field(default_factory=dict)
    model_counts: dict[str, int] = field(default_factory=dict)
    zone_max_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera_id,
            "bucket_start": self.bucket_start.isoformat(),
            "samples": self.samples,
            "latest_counts": dict(self.latest_counts),
            "max_counts": dict(self.max_counts),
            "model_counts": dict(self.model_counts),
            "zone_max_counts": dict(self.zone_max_counts),
        }


class ObservationStore:
    """Thread-safe per-minute observation buckets with a hard memory bound."""

    def __init__(self, max_buckets_per_camera: int = 1440):
        self.max_buckets_per_camera = max(1, int(max_buckets_per_camera))
        self._buckets: dict[str, deque[ObservationBucket]] = {}
        self._lock = threading.RLock()

    def record(
        self,
        camera_id: str,
        timestamp: datetime,
        detections_by_model: dict[str, list[Detection]],
        zones: dict[str, Any] | None = None,
    ) -> ObservationBucket:
        bucket_start = timestamp.replace(second=0, microsecond=0)
        detections = [item for values in detections_by_model.values() for item in values]
        counts = Counter(item.class_name for item in detections)
        model_counts = {name: len(values) for name, values in detections_by_model.items()}
        zone_counts = self._zone_counts(detections, zones or {})

        with self._lock:
            camera_buckets = self._buckets.setdefault(
                camera_id, deque(maxlen=self.max_buckets_per_camera)
            )
            if not camera_buckets or camera_buckets[-1].bucket_start != bucket_start:
                camera_buckets.append(ObservationBucket(camera_id, bucket_start))
            bucket = camera_buckets[-1]
            bucket.samples += 1
            bucket.latest_counts = dict(counts)
            bucket.model_counts = model_counts
            for name, count in counts.items():
                bucket.max_counts[name] = max(bucket.max_counts.get(name, 0), count)
            for name, count in zone_counts.items():
                bucket.zone_max_counts[name] = max(bucket.zone_max_counts.get(name, 0), count)
            return bucket

    def query(self, camera_id: str | None = None, limit: int = 120) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 2000))
        with self._lock:
            if camera_id is not None:
                values = list(self._buckets.get(camera_id, ()))
            else:
                values = [bucket for buckets in self._buckets.values() for bucket in buckets]
            values.sort(key=lambda item: item.bucket_start, reverse=True)
            return [item.to_dict() for item in values[:safe_limit]]

    def reset(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id is None:
                self._buckets.clear()
            else:
                self._buckets.pop(camera_id, None)

    @classmethod
    def _zone_counts(
        cls,
        detections: list[Detection],
        zones: dict[str, Any],
    ) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for zone_id, zone in zones.items():
            points = zone.get("points", []) if isinstance(zone, dict) else []
            if len(points) < 3:
                continue
            for detection in detections:
                x1, y1, x2, y2 = detection.bbox
                if cls._point_in_polygon(((x1 + x2) / 2, (y1 + y2) / 2), points):
                    counts[f"{zone_id}:{detection.class_name}"] += 1
        return dict(counts)

    @staticmethod
    def _point_in_polygon(point: tuple[float, float], polygon: list) -> bool:
        x, y = point
        inside = False
        p1x, p1y = polygon[0]
        for index in range(len(polygon) + 1):
            p2x, p2y = polygon[index % len(polygon)]
            if min(p1y, p2y) < y <= max(p1y, p2y) and x <= max(p1x, p2x):
                intersect = (
                    (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1y != p2y
                    else p1x
                )
                if p1x == p2x or x <= intersect:
                    inside = not inside
            p1x, p1y = p2x, p2y
        return inside
