"""Track-based loitering monitor.

Emits one event per (camera, zone, track) episode when a qualifying track remains
inside a polygon zone longer than the configured dwell time. Short occlusions
within ``grace_sec`` preserve the episode; leaving the zone or exceeding the
grace window clears state so a new entry starts a fresh dwell timer.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from src.tracking.models import TrackSnapshot


@dataclass(frozen=True)
class LoiteringCandidate:
    camera_id: str
    track: TrackSnapshot
    zone_id: str
    dwell_sec: float
    first_in_zone: datetime
    timestamp: datetime
    observed_samples: int
    max_confidence: float


@dataclass
class _Episode:
    first_in_zone: datetime
    last_observed: datetime
    fired: bool
    observed_samples: int
    max_confidence: float
    missing: bool = False


class LoiteringMonitor:
    def __init__(self):
        self._episodes: dict[tuple[str, str, int], _Episode] = {}
        self._last_emitted: dict[tuple[str, str], datetime] = {}
        self._stats: dict[str, dict[str, int]] = {}
        self._lock = threading.RLock()

    def evaluate(
        self,
        camera_id: str,
        tracks: list[TrackSnapshot],
        zones: dict[str, Any],
        timestamp: datetime,
        policy: dict[str, Any] | None = None,
    ) -> list[LoiteringCandidate]:
        if policy is None:
            return []
        rule = policy.get("rule", {}) or {}
        trigger = policy.get("trigger", {}) or {}
        zone_id = str(rule.get("zone_id", "loitering"))
        dwell_sec = max(0.0, float(rule.get("dwell_time_sec", 60.0)))
        grace = timedelta(seconds=max(0.0, float(rule.get("grace_sec", 3.0))))
        cooldown = timedelta(seconds=max(0.0, float(policy.get("cooldown_sec", 120.0))))
        classes = {str(name) for name in trigger.get("classes", []) if name}
        min_confidence = float(trigger.get("min_confidence", 0.0))
        zone = zones.get(zone_id) if isinstance(zones, dict) else None
        candidates: list[LoiteringCandidate] = []
        if not isinstance(zone, dict) or zone.get("type") == "line":
            self._expire_zone(camera_id, zone_id)
            return candidates
        points = zone.get("points", [])
        if not isinstance(points, list) or len(points) < 3:
            self._expire_zone(camera_id, zone_id)
            return candidates

        observed_keys: set[tuple[str, str, int]] = set()
        for track in tracks:
            confident = track.confidence >= min_confidence
            if classes and track.class_name not in classes:
                continue
            if not confident:
                continue
            footpoint = self._footpoint(track.bbox)
            inside = self._point_in_polygon(footpoint, points)
            key = (camera_id, zone_id, track.track_id)
            observed_keys.add(key)
            with self._lock:
                if not inside:
                    self._episodes.pop(key, None)
                    continue
                episode = self._episodes.get(key)
                if episode is not None and (
                    timestamp < episode.last_observed
                    or (
                        episode.missing
                        and timestamp - episode.last_observed > grace
                    )
                ):
                    episode = None
                if episode is None:
                    episode = _Episode(
                        first_in_zone=timestamp,
                        last_observed=timestamp,
                        fired=False,
                        observed_samples=1,
                        max_confidence=track.confidence,
                    )
                    self._episodes[key] = episode
                else:
                    episode.last_observed = timestamp
                    episode.missing = False
                    episode.max_confidence = max(episode.max_confidence, track.confidence)
                    episode.observed_samples += 1

                if episode.fired:
                    continue

                dwell = (timestamp - episode.first_in_zone).total_seconds()
                if dwell >= dwell_sec:
                    episode.fired = True
                    cooldown_key = (camera_id, zone_id)
                    last_emitted = self._last_emitted.get(cooldown_key)
                    if (
                        last_emitted is not None
                        and timestamp >= last_emitted
                        and timestamp - last_emitted < cooldown
                    ):
                        stats = self._stats.setdefault(camera_id, self._empty_stats())
                        stats["cooldown_suppressed"] += 1
                        continue
                    self._last_emitted[cooldown_key] = timestamp
                    stats = self._stats.setdefault(camera_id, self._empty_stats())
                    stats["emitted"] += 1
                    candidates.append(LoiteringCandidate(
                        camera_id=camera_id,
                        track=track,
                        zone_id=zone_id,
                        dwell_sec=dwell,
                        first_in_zone=episode.first_in_zone,
                        timestamp=timestamp,
                        observed_samples=episode.observed_samples,
                        max_confidence=episode.max_confidence,
                    ))

        self._expire_missing(camera_id, zone_id, timestamp, grace, observed_keys)
        return candidates

    def reset(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id is None:
                self._episodes.clear()
                self._last_emitted.clear()
                self._stats.clear()
                return
            for key in list(self._episodes):
                if key[0] == camera_id:
                    self._episodes.pop(key, None)
            for emitted_key in list(self._last_emitted):
                if emitted_key[0] == camera_id:
                    self._last_emitted.pop(emitted_key, None)
            self._stats.pop(camera_id, None)

    def stats(self, camera_id: str) -> dict[str, int]:
        with self._lock:
            values = dict(self._stats.get(camera_id, self._empty_stats()))
            values["active_episodes"] = sum(
                1 for key in self._episodes if key[0] == camera_id
            )
            return values

    @staticmethod
    def _empty_stats() -> dict[str, int]:
        return {"emitted": 0, "cooldown_suppressed": 0}

    def _expire_zone(self, camera_id: str, zone_id: str) -> None:
        with self._lock:
            for key in list(self._episodes):
                if key[0] == camera_id and key[1] == zone_id:
                    self._episodes.pop(key, None)

    def _expire_missing(
        self,
        camera_id: str,
        zone_id: str,
        timestamp: datetime,
        grace: timedelta,
        observed_keys: set[tuple[str, str, int]],
    ) -> None:
        with self._lock:
            for key in list(self._episodes):
                if key[0] != camera_id or key[1] != zone_id:
                    continue
                if key in observed_keys:
                    continue
                episode = self._episodes[key]
                episode.missing = True
                if timestamp < episode.last_observed or timestamp - episode.last_observed > grace:
                    self._episodes.pop(key, None)

    @staticmethod
    def _footpoint(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
        x1, _y1, x2, y2 = bbox
        return ((x1 + x2) / 2.0, float(y2))

    @staticmethod
    def _point_in_polygon(point: tuple[float, float], polygon: list) -> bool:
        x, y = point
        for index, start in enumerate(polygon):
            end = polygon[(index + 1) % len(polygon)]
            if LoiteringMonitor._point_on_segment(point, start, end):
                return True
        inside = False
        p1x, p1y = float(polygon[0][0]), float(polygon[0][1])
        for index in range(len(polygon) + 1):
            p2x = float(polygon[index % len(polygon)][0])
            p2y = float(polygon[index % len(polygon)][1])
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

    @staticmethod
    def _point_on_segment(point: tuple[float, float], start: list, end: list) -> bool:
        x, y = point
        x1, y1 = float(start[0]), float(start[1])
        x2, y2 = float(end[0]), float(end[1])
        cross = (x - x1) * (y2 - y1) - (y - y1) * (x2 - x1)
        if abs(cross) > 1e-6:
            return False
        return (
            min(x1, x2) - 1e-6 <= x <= max(x1, x2) + 1e-6
            and min(y1, y2) - 1e-6 <= y <= max(y1, y2) + 1e-6
        )
