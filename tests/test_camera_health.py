"""Camera ingest health state machine and pipeline integration tests."""
from unittest.mock import Mock

from src.events.camera_health import (
    CameraHealthMonitor,
    CameraHealthState,
    CameraHealthTransition,
)
from src.pipeline.pipeline import SafetyPipeline


def _status(frame_count: int, elapsed: float | None, *, stale: bool = False) -> dict:
    return {
        "frame_count": frame_count,
        "seconds_since_last_frame": elapsed,
        "is_stale": stale,
    }


def test_initializing_disconnect_and_recovery_are_deduplicated():
    monitor = CameraHealthMonitor(stale_after_sec=10, disconnect_after_sec=30)

    assert monitor.update("cam", _status(0, None), now=0) is None
    assert monitor.update("cam", _status(0, None), now=29) is None
    disconnected = monitor.update("cam", _status(0, None), now=30)
    assert disconnected is not None
    assert disconnected.event_type == "camera_disconnect"
    assert disconnected.current == CameraHealthState.DISCONNECTED
    assert monitor.update("cam", _status(0, None), now=60) is None

    recovered = monitor.update("cam", _status(1, 0.1), now=61)
    assert recovered is not None
    assert recovered.event_type == "camera_recovered"
    assert recovered.current == CameraHealthState.RECEIVING


def test_receiving_becomes_stale_before_disconnect():
    monitor = CameraHealthMonitor(stale_after_sec=10, disconnect_after_sec=30)
    monitor.update("cam", _status(1, 0), now=0)

    stale = monitor.update("cam", _status(1, 10), now=10)
    assert stale is not None
    assert stale.current == CameraHealthState.STALE
    assert stale.event_type is None

    disconnected = monitor.update("cam", _status(1, 30), now=30)
    assert disconnected is not None
    assert disconnected.event_type == "camera_disconnect"


def test_pipeline_emits_health_events_only_when_camera_opted_in():
    pipeline = SafetyPipeline(detector=Mock())
    transition = CameraHealthTransition(
        camera_id="cam",
        previous=CameraHealthState.STALE,
        current=CameraHealthState.DISCONNECTED,
        event_type="camera_disconnect",
    )
    pipeline.camera_health_monitor.update = Mock(return_value=transition)
    received = []
    pipeline.add_alert_callback(received.append)

    pipeline.set_camera_profile("cam", {"domain": "general", "enabled_events": []})
    pipeline._process_camera_health("cam", {})
    assert received == []

    pipeline.set_camera_profile(
        "cam", {"domain": "general", "enabled_events": ["camera_disconnect"]}
    )
    pipeline._process_camera_health("cam", {})
    assert len(received) == 1
    assert received[0].event_type == "camera_disconnect"
    assert received[0].alert_state == "confirmed"
    assert received[0].vlm_needed is False
