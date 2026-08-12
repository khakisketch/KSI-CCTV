"""Camera tamper persistence and pipeline opt-in tests."""
from unittest.mock import Mock

import numpy as np

from src.events.tamper import TamperMonitor, TamperTransition
from src.pipeline.pipeline import SafetyPipeline


def _textured_frame() -> np.ndarray:
    grid = np.indices((120, 160)).sum(axis=0) % 2
    gray = (grid * 220 + 20).astype(np.uint8)
    return np.repeat(gray[:, :, None], 3, axis=2)


def test_tamper_requires_sustained_signal_and_recovers_once():
    monitor = TamperMonitor(duration_sec=5, recovery_sec=3, warmup_samples=3)
    normal = _textured_frame()
    covered = np.zeros_like(normal)

    for now in (0, 1, 2):
        assert monitor.update("cam", normal, now=now) is None
    tamper = None
    for now in (3, 4, 5, 6, 7, 8):
        tamper = monitor.update("cam", covered, now=now)
    assert tamper is not None
    assert tamper.event_type == "tamper"
    assert tamper.reason == "flat_dark"
    assert monitor.update("cam", covered, now=20) is None

    recovered = None
    for now in (21, 22, 23, 24):
        recovered = monitor.update("cam", normal, now=now)
    assert recovered is not None
    assert recovered.event_type == "tamper_recovered"
    assert monitor.state("cam") == "normal"


def test_pipeline_tamper_event_is_blocked_without_release_qualification():
    pipeline = SafetyPipeline(detector=Mock())
    transition = TamperTransition(
        camera_id="cam",
        event_type="tamper",
        reason="flat_frame",
        metrics={"brightness": 1.0, "contrast": 0.1, "sharpness": 0.0},
    )
    pipeline.tamper_monitor.update = Mock(return_value=transition)
    alerts = []
    pipeline.add_alert_callback(alerts.append)
    frame = np.zeros((20, 20, 3), dtype=np.uint8)

    pipeline.set_camera_profile("cam", {"domain": "general", "enabled_events": []})
    pipeline._process_camera_tamper("cam", frame)
    assert alerts == []

    pipeline.set_camera_profile("cam", {"domain": "general", "enabled_events": ["tamper"]})
    pipeline._process_camera_tamper("cam", frame)
    assert alerts == []


def test_tamper_timer_resets_after_frame_gap():
    monitor = TamperMonitor(
        duration_sec=5,
        recovery_sec=3,
        warmup_samples=1,
        max_sample_gap_sec=2,
    )
    covered = np.zeros((20, 20, 3), dtype=np.uint8)

    assert monitor.update("cam", covered, now=0) is None
    assert monitor.update("cam", covered, now=100) is None
    transition = None
    for now in (101, 102, 103, 104, 105):
        transition = monitor.update("cam", covered, now=now)
    assert transition is not None
    assert transition.event_type == "tamper"

    normal = _textured_frame()
    assert monitor.update("cam", normal, now=106) is None
    assert monitor.update("cam", normal, now=200) is None
    monitor.update("cam", normal, now=201)
    monitor.update("cam", normal, now=202)
    recovered = monitor.update("cam", normal, now=203)
    assert recovered is not None
    assert recovered.event_type == "tamper_recovered"
