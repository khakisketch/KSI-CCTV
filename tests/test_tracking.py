"""ByteTrack adapter and camera isolation tests."""
import threading
from datetime import datetime, timedelta

from src.detection.yolo_detector import Detection
from src.tracking import (
    ByteTrackManager,
    LineCrossingMonitor,
    LoiteringMonitor,
    TrackSnapshot,
)
from src.tracking.models import TrackPoint


def _person(bbox, confidence=0.9):
    return Detection(0, "person", confidence, bbox)


def _track(
    track_id: int,
    timestamp: datetime,
    bbox: tuple[int, int, int, int] = (40, 20, 60, 80),
    class_name: str = "person",
    confidence: float = 0.9,
) -> TrackSnapshot:
    return TrackSnapshot(
        camera_id="cam",
        track_id=track_id,
        class_name=class_name,
        bbox=bbox,
        confidence=confidence,
        first_seen=timestamp,
        last_seen=timestamp,
        trajectory=(TrackPoint((bbox[0] + bbox[2]) / 2, bbox[3], timestamp),),
    )


def _loitering_policy(
    dwell_time_sec: float = 60,
    grace_sec: float = 3,
    cooldown_sec: float = 0,
) -> dict:
    return {
        "trigger": {"classes": ["person"], "min_confidence": 0.4},
        "rule": {
            "zone_id": "loitering",
            "dwell_time_sec": dwell_time_sec,
            "grace_sec": grace_sec,
        },
        "cooldown_sec": cooldown_sec,
    }


def test_bytetrack_keeps_identity_and_trajectory():
    manager = ByteTrackManager(track_high_thresh=0.25, new_track_thresh=0.25)
    start = datetime(2026, 7, 19, 12, 0)

    first = manager.update("cam", [_person((10, 10, 30, 50))], start)
    second = manager.update(
        "cam", [_person((15, 10, 35, 50))], start + timedelta(milliseconds=200)
    )

    assert first
    assert second
    assert first[0].track_id == second[0].track_id
    assert len(second[0].trajectory) == 2
    assert second[0].centroid[0] > first[0].centroid[0]
    assert second[0].centroid[1] == 30.0


def test_bytetrack_isolates_and_resets_camera_state():
    manager = ByteTrackManager(track_high_thresh=0.25, new_track_thresh=0.25)
    timestamp = datetime(2026, 7, 19, 12, 0)
    cam_a = manager.update("cam-a", [_person((10, 10, 30, 50))], timestamp)
    cam_b = manager.update("cam-b", [_person((10, 10, 30, 50))], timestamp)
    assert cam_a and cam_b
    assert cam_a[0].camera_id == "cam-a"
    assert cam_b[0].camera_id == "cam-b"

    manager.reset("cam-a")
    restarted = manager.update(
        "cam-a", [_person((100, 10, 120, 50))], timestamp + timedelta(seconds=1)
    )
    assert restarted
    assert len(restarted[0].trajectory) == 1


def test_bytetrack_discards_update_completed_after_reset():
    manager = ByteTrackManager()
    entered = threading.Event()
    release = threading.Event()

    class SlowTracker:
        def update(self, _results):
            entered.set()
            release.wait(timeout=2)
            return [[10, 10, 30, 50, 1, 0.9, 0]]

    tracker = SlowTracker()
    manager._trackers["cam"] = tracker
    results = []
    worker = threading.Thread(
        target=lambda: results.extend(
            manager.update("cam", [_person((10, 10, 30, 50))], datetime.now())
        )
    )
    worker.start()
    assert entered.wait(timeout=2)
    manager.reset("cam")
    release.set()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert results == []
    assert not any(key[0] == "cam" for key in manager._history)


def test_line_crossing_emits_once_per_track_cooldown():
    monitor = LineCrossingMonitor(cooldown_sec=5)
    timestamp = datetime(2026, 7, 19, 12, 0, 1)
    track = TrackSnapshot(
        camera_id="cam",
        track_id=7,
        class_name="person",
        bbox=(40, 60, 60, 100),
        confidence=0.9,
        first_seen=timestamp - timedelta(seconds=1),
        last_seen=timestamp,
        trajectory=(
            TrackPoint(50, 40, timestamp - timedelta(milliseconds=200)),
            TrackPoint(50, 80, timestamp),
        ),
    )
    zones = {"line": {"type": "line", "points": [[0, 60], [100, 60]], "direction": "any"}}

    first = monitor.evaluate("cam", [track], zones, timestamp)
    duplicate = monitor.evaluate("cam", [track], zones, timestamp + timedelta(seconds=1))

    assert len(first) == 1
    assert first[0].track.track_id == 7
    assert first[0].direction == "positive"
    assert duplicate == []


def test_line_crossing_honors_direction_and_reset():
    monitor = LineCrossingMonitor(cooldown_sec=30)
    timestamp = datetime(2026, 7, 19, 12, 0, 1)
    track = TrackSnapshot(
        camera_id="cam",
        track_id=9,
        class_name="person",
        bbox=(40, 60, 60, 100),
        confidence=0.9,
        first_seen=timestamp,
        last_seen=timestamp,
        trajectory=(TrackPoint(50, 40, timestamp), TrackPoint(50, 80, timestamp)),
    )
    blocked = {"line": {"type": "line", "points": [[0, 60], [100, 60]], "direction": "negative"}}
    allowed = {"line": {"type": "line", "points": [[0, 60], [100, 60]], "direction": "positive"}}

    assert monitor.evaluate("cam", [track], blocked, timestamp) == []
    monitor.reset("cam")
    assert monitor.evaluate("cam", [track], allowed, timestamp)
    monitor.reset("cam")
    assert monitor.evaluate("cam", [track], allowed, timestamp + timedelta(seconds=1))


def test_line_crossing_requires_intersection_with_finite_line():
    monitor = LineCrossingMonitor()
    timestamp = datetime(2026, 7, 19, 12, 0, 1)
    track = TrackSnapshot(
        camera_id="cam",
        track_id=10,
        class_name="person",
        bbox=(140, 60, 160, 100),
        confidence=0.9,
        first_seen=timestamp,
        last_seen=timestamp,
        trajectory=(TrackPoint(150, 40, timestamp), TrackPoint(150, 80, timestamp)),
    )
    zones = {"line": {"type": "line", "points": [[0, 60], [100, 60]]}}

    assert monitor.evaluate("cam", [track], zones, timestamp) == []


def test_line_crossing_handles_frame_on_line():
    monitor = LineCrossingMonitor()
    start = datetime(2026, 7, 19, 12, 0)
    zones = {"line": {"type": "line", "points": [[0, 60], [100, 60]]}}

    before = _track(11, start, bbox=(40, 0, 60, 40))
    on_line = _track(11, start + timedelta(seconds=1), bbox=(40, 20, 60, 60))
    after = _track(11, start + timedelta(seconds=2), bbox=(40, 40, 60, 80))

    assert monitor.evaluate("cam", [before], zones, start) == []
    assert monitor.evaluate("cam", [on_line], zones, start + timedelta(seconds=1)) == []
    assert len(monitor.evaluate("cam", [after], zones, start + timedelta(seconds=2))) == 1


def test_line_crossing_uses_policy_cooldown_override():
    monitor = LineCrossingMonitor(cooldown_sec=1)
    start = datetime(2026, 7, 19, 12, 0)
    zones = {"line": {"type": "line", "points": [[0, 60], [100, 60]]}}

    def crossing(timestamp, first_y, second_y):
        return TrackSnapshot(
            camera_id="cam",
            track_id=15,
            class_name="person",
            bbox=(40, second_y, 60, second_y + 20),
            confidence=0.9,
            first_seen=start,
            last_seen=timestamp,
            trajectory=(
                TrackPoint(50, first_y, timestamp - timedelta(milliseconds=100)),
                TrackPoint(50, second_y, timestamp),
            ),
        )

    assert monitor.evaluate(
        "cam", [crossing(start, 40, 80)], zones, start, cooldown_sec=30
    )
    assert monitor.evaluate(
        "cam", [crossing(start + timedelta(seconds=10), 80, 40)], zones,
        start + timedelta(seconds=10), cooldown_sec=30,
    ) == []
    assert monitor.evaluate(
        "cam", [crossing(start + timedelta(seconds=31), 40, 80)], zones,
        start + timedelta(seconds=31), cooldown_sec=30,
    )
    assert monitor.stats("cam")["cooldown_suppressed"] == 1


def test_loitering_starts_dwell_on_zone_entry_and_emits_once():
    monitor = LoiteringMonitor()
    start = datetime(2026, 7, 19, 12, 0)
    zones = {
        "loitering": {
            "type": "polygon",
            "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
        }
    }
    policy = _loitering_policy(dwell_time_sec=60)

    assert monitor.evaluate("cam", [_track(1, start, (140, 20, 160, 80))], zones, start, policy) == []
    entered = start + timedelta(seconds=10)
    assert monitor.evaluate("cam", [_track(1, entered)], zones, entered, policy) == []
    assert monitor.evaluate("cam", [_track(1, entered + timedelta(seconds=59))], zones, entered + timedelta(seconds=59), policy) == []
    candidates = monitor.evaluate(
        "cam", [_track(1, entered + timedelta(seconds=60))], zones,
        entered + timedelta(seconds=60), policy,
    )
    assert len(candidates) == 1
    assert candidates[0].first_in_zone == entered
    assert candidates[0].dwell_sec == 60
    assert monitor.evaluate(
        "cam", [_track(1, entered + timedelta(seconds=70))], zones,
        entered + timedelta(seconds=70), policy,
    ) == []


def test_loitering_isolates_tracks_and_preserves_short_occlusion():
    monitor = LoiteringMonitor()
    start = datetime(2026, 7, 19, 12, 0)
    zones = {
        "loitering": {
            "type": "polygon",
            "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
        }
    }
    policy = _loitering_policy(dwell_time_sec=3, grace_sec=3)

    assert monitor.evaluate("cam", [_track(1, start)], zones, start, policy) == []
    assert monitor.evaluate("cam", [], zones, start + timedelta(seconds=2), policy) == []
    candidates = monitor.evaluate(
        "cam", [_track(1, start + timedelta(seconds=3))], zones,
        start + timedelta(seconds=3), policy,
    )
    assert len(candidates) == 1

    monitor.reset("cam")
    assert monitor.evaluate("cam", [_track(1, start)], zones, start, policy) == []
    assert monitor.evaluate(
        "cam", [_track(2, start + timedelta(seconds=2))], zones,
        start + timedelta(seconds=2), policy,
    ) == []
    assert monitor.evaluate(
        "cam", [_track(2, start + timedelta(seconds=4))], zones,
        start + timedelta(seconds=4), policy,
    ) == []


def test_loitering_clears_after_exit_or_long_occlusion_and_rearms():
    monitor = LoiteringMonitor()
    start = datetime(2026, 7, 19, 12, 0)
    zones = {
        "loitering": {
            "type": "polygon",
            "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
        }
    }
    policy = _loitering_policy(dwell_time_sec=2, grace_sec=1)

    monitor.evaluate("cam", [_track(1, start)], zones, start, policy)
    assert monitor.evaluate(
        "cam", [_track(1, start + timedelta(seconds=2))], zones,
        start + timedelta(seconds=2), policy,
    )
    monitor.evaluate(
        "cam", [_track(1, start + timedelta(seconds=3), (140, 20, 160, 80))],
        zones, start + timedelta(seconds=3), policy,
    )
    monitor.evaluate("cam", [_track(1, start + timedelta(seconds=4))], zones, start + timedelta(seconds=4), policy)
    assert monitor.evaluate(
        "cam", [_track(1, start + timedelta(seconds=6))], zones,
        start + timedelta(seconds=6), policy,
    )

    monitor.reset("cam")
    monitor.evaluate("cam", [_track(2, start)], zones, start, policy)
    monitor.evaluate("cam", [], zones, start + timedelta(seconds=2), policy)
    assert monitor.evaluate(
        "cam", [_track(2, start + timedelta(seconds=3))], zones,
        start + timedelta(seconds=3), policy,
    ) == []


def test_loitering_zone_cooldown_suppresses_new_track_episode():
    monitor = LoiteringMonitor()
    start = datetime(2026, 7, 19, 12, 0)
    zones = {
        "loitering": {
            "type": "polygon",
            "points": [[0, 0], [100, 0], [100, 100], [0, 100]],
        }
    }
    policy = _loitering_policy(dwell_time_sec=1, cooldown_sec=10)

    monitor.evaluate("cam", [_track(1, start)], zones, start, policy)
    assert monitor.evaluate("cam", [_track(1, start + timedelta(seconds=1))], zones, start + timedelta(seconds=1), policy)
    monitor.evaluate("cam", [_track(2, start + timedelta(seconds=2))], zones, start + timedelta(seconds=2), policy)
    assert monitor.evaluate("cam", [_track(2, start + timedelta(seconds=3))], zones, start + timedelta(seconds=3), policy) == []
    assert monitor.stats("cam")["cooldown_suppressed"] == 1
    monitor.evaluate("cam", [_track(3, start + timedelta(seconds=11))], zones, start + timedelta(seconds=11), policy)
    assert monitor.evaluate("cam", [_track(3, start + timedelta(seconds=12))], zones, start + timedelta(seconds=12), policy)
