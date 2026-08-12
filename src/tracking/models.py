"""Tracking output models kept separate from detector results."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TrackPoint:
    x: float
    y: float
    timestamp: datetime


@dataclass(frozen=True)
class TrackSnapshot:
    camera_id: str
    track_id: int
    class_name: str
    bbox: tuple[int, int, int, int]
    confidence: float
    first_seen: datetime
    last_seen: datetime
    trajectory: tuple[TrackPoint, ...]

    @property
    def centroid(self) -> tuple[float, float]:
        x1, y1, x2, y2 = self.bbox
        return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
