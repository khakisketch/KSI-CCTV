"""Legacy fallback opt-in regression tests.

detector-only 모드에서 `_dispatch_frame`은 env `KSI_CCTV_LEGACY_FALLBACK`
명시 opt-in 없이는 legacy 실행을 스킵한다. `_process_frame_legacy`
직접 호출은 env와 무관하게 기존 동작을 유지한다.
"""
import logging
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

sys.path.append(str(Path(__file__).parent.parent))

from src.pipeline.pipeline import SafetyPipeline


def _make_pipeline(monkeypatch, detector, env_value="__unset__"):
    if env_value == "__unset__":
        monkeypatch.delenv("KSI_CCTV_LEGACY_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("KSI_CCTV_LEGACY_FALLBACK", env_value)
    return SafetyPipeline(detector=detector)


def test_dispatch_without_optin_skips_detector(monkeypatch, caplog):
    detector = Mock()
    pipeline = _make_pipeline(monkeypatch, detector)
    assert pipeline.use_policy_mode is False
    assert pipeline.legacy_fallback_enabled is False

    with caplog.at_level(logging.ERROR, logger="src.pipeline.pipeline"):
        pipeline._dispatch_frame("cam", Mock(), datetime.now())

    detector.detect.assert_not_called()
    assert any(
        "KSI_CCTV_LEGACY_FALLBACK" in r.message for r in caplog.records
    )


def test_dispatch_with_optin_calls_detector(monkeypatch, caplog):
    detector = Mock()
    detector.detect.return_value = []
    pipeline = _make_pipeline(monkeypatch, detector, env_value="1")
    assert pipeline.legacy_fallback_enabled is True

    with caplog.at_level(logging.WARNING, logger="src.pipeline.pipeline"):
        pipeline._dispatch_frame("cam", Mock(), datetime.now())

    detector.detect.assert_called_once()
    assert any("legacy" in r.message.lower() for r in caplog.records)


def test_direct_legacy_call_works_without_optin(monkeypatch):
    detector = Mock()
    detector.detect.return_value = []
    pipeline = _make_pipeline(monkeypatch, detector)
    assert pipeline.legacy_fallback_enabled is False

    pipeline._process_frame_legacy("cam", Mock(), datetime.now())

    detector.detect.assert_called_once()
