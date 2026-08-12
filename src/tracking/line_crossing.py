"""Track trajectory line-crossing rule."""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.tracking.models import TrackSnapshot


@dataclass(frozen=True)
class LineCrossingCandidate:
    camera_id: str
    track: TrackSnapshot
    zone_id: str
    direction: str
    configured_direction: str
    line_points: tuple[tuple[float, float], tuple[float, float]]
    from_side: str
    to_side: str
    previous_centroid: tuple[float, float]
    current_centroid: tuple[float, float]
    cooldown_sec: float
    timestamp: datetime


class LineCrossingMonitor:
    def __init__(self, cooldown_sec: float = 5.0, min_displacement: float = 0.0):
        self.default_cooldown_sec = max(0.0, float(cooldown_sec))
        self.min_displacement = max(0.0, float(min_displacement))
        self._last_crossing: dict[tuple[str, str, int], float] = {}
        self._last_side: dict[
            tuple[str, str, int], tuple[float, tuple[float, float]]
        ] = {}
        self._last_observed: dict[tuple[str, str, int], float] = {}
        self._stats: dict[str, dict[str, int]] = {}
        self._lock = threading.RLock()

    def evaluate(
        self,
        camera_id: str,
        tracks: list[TrackSnapshot],
        zones: dict[str, Any],
        timestamp: datetime,
        cooldown_sec: float | None = None,
        min_displacement: float | None = None,
    ) -> list[LineCrossingCandidate]:
        cooldown = (
            self.default_cooldown_sec
            if cooldown_sec is None
            else max(0.0, float(cooldown_sec))
        )
        minimum_movement = (
            self.min_displacement
            if min_displacement is None
            else max(0.0, float(min_displacement))
        )
        candidates: list[LineCrossingCandidate] = []
        now_value = timestamp.timestamp()
        for zone_id, zone in zones.items():
            if not isinstance(zone, dict) or zone.get("type") != "line":
                continue
            points = zone.get("points", [])
            if len(points) != 2:
                continue
            configured_direction = str(zone.get("direction", "any"))
            start = (float(points[0][0]), float(points[0][1]))
            end = (float(points[1][0]), float(points[1][1]))
            for track in tracks:
                if not track.trajectory:
                    continue
                current = track.trajectory[-1]
                current_point = (current.x, current.y)
                current_side = self._side(start, end, current_point)
                key = (camera_id, zone_id, track.track_id)
                with self._lock:
                    previous_state = self._last_side.get(key)
                    if previous_state is None and len(track.trajectory) >= 2:
                        previous = track.trajectory[-2]
                        previous_point = (previous.x, previous.y)
                        previous_side = self._side(start, end, previous_point)
                        if previous_side != 0:
                            previous_state = (previous_side, previous_point)
                    self._last_observed[key] = now_value
                    if current_side != 0:
                        self._last_side[key] = (current_side, current_point)
                if current_side == 0 or previous_state is None:
                    continue
                previous_side, previous_point = previous_state
                if previous_side * current_side >= 0:
                    continue
                displacement = (
                    (current_point[0] - previous_point[0]) ** 2
                    + (current_point[1] - previous_point[1]) ** 2
                ) ** 0.5
                if minimum_movement > 0.0 and displacement < minimum_movement:
                    continue
                if not self._segments_intersect(previous_point, current_point, start, end):
                    continue
                with self._lock:
                    stats = self._stats.setdefault(camera_id, self._empty_stats())
                    stats["candidates"] += 1
                direction = "positive" if previous_side < 0 < current_side else "negative"
                if configured_direction not in {"any", direction}:
                    with self._lock:
                        stats["direction_suppressed"] += 1
                    continue
                with self._lock:
                    last = self._last_crossing.get(key)
                    if (
                        last is not None
                        and now_value >= last
                        and now_value - last < cooldown
                    ):
                        stats["cooldown_suppressed"] += 1
                        continue
                    self._last_crossing[key] = now_value
                    stats["emitted"] += 1
                candidates.append(LineCrossingCandidate(
                    camera_id=camera_id,
                    track=track,
                    zone_id=zone_id,
                    direction=direction,
                    configured_direction=configured_direction,
                    line_points=(start, end),
                    from_side="negative" if previous_side < 0 else "positive",
                    to_side="positive" if current_side > 0 else "negative",
                    previous_centroid=previous_point,
                    current_centroid=current_point,
                    cooldown_sec=cooldown,
                    timestamp=timestamp,
                ))
        self._prune(camera_id, now_value, max(60.0, cooldown * 2.0))
        return candidates

    def reset(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id is None:
                self._last_crossing.clear()
                self._last_side.clear()
                self._last_observed.clear()
                self._stats.clear()
                return
            for state in (self._last_crossing, self._last_side, self._last_observed):
                for key in list(state):
                    if key[0] == camera_id:
                        state.pop(key, None)
            self._stats.pop(camera_id, None)

    def stats(self, camera_id: str) -> dict[str, int]:
        with self._lock:
            return dict(self._stats.get(camera_id, self._empty_stats()))

    @staticmethod
    def _empty_stats() -> dict[str, int]:
        return {
            "candidates": 0,
            "emitted": 0,
            "cooldown_suppressed": 0,
            "direction_suppressed": 0,
        }

    def _prune(self, camera_id: str, now_value: float, stale_sec: float) -> None:
        with self._lock:
            stale = {
                key
                for key, last_seen in self._last_observed.items()
                if key[0] == camera_id and now_value - last_seen > stale_sec
            }
            for key in stale:
                self._last_observed.pop(key, None)
                self._last_side.pop(key, None)
                self._last_crossing.pop(key, None)

    @staticmethod
    def _side(
        start: tuple[float, float],
        end: tuple[float, float],
        point: tuple[float, float],
    ) -> float:
        return (
            (end[0] - start[0]) * (point[1] - start[1])
            - (end[1] - start[1]) * (point[0] - start[0])
        )

    @staticmethod
    def _segments_intersect(
        first_start: tuple[float, float],
        first_end: tuple[float, float],
        second_start: tuple[float, float],
        second_end: tuple[float, float],
    ) -> bool:
        def orientation(
            start: tuple[float, float],
            end: tuple[float, float],
            point: tuple[float, float],
        ) -> float:
            return LineCrossingMonitor._side(start, end, point)

        first_a = orientation(first_start, first_end, second_start)
        first_b = orientation(first_start, first_end, second_end)
        second_a = orientation(second_start, second_end, first_start)
        second_b = orientation(second_start, second_end, first_end)
        return first_a * first_b <= 0 and second_a * second_b <= 0
