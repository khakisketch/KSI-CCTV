"""파이프라인 단일경로 회귀 테스트 (U3).

- 카메라 프로필은 정식형 {domain, enabled_events, zones, detection_type}으로
  정규화된다. 구 별칭(events, detection_type/profile)도 받아들인다.
- enabled_events=[] (전부 비활성)와 None (필터 없음)을 구분해 보존한다.
- legacy 단일-detector 경로는 유지되나 최초 사용 시 deprecation 경고를 남긴다.
"""
import logging
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.append(str(Path(__file__).parent.parent))

from src.pipeline.pipeline import SafetyPipeline


def test_alias_profile_is_normalized_to_canonical():
    pipeline = SafetyPipeline(detector=Mock())
    pipeline.set_camera_profile(
        "cam", {"detection_type": "school", "events": ["fire"], "zones": {}}
    )
    stored = pipeline._camera_profiles["cam"]
    assert stored["domain"] == "school"
    assert stored["enabled_events"] == ["fire"]
    assert stored["detection_type"] == "school"


def test_empty_enabled_events_is_preserved_not_none():
    pipeline = SafetyPipeline(detector=Mock())
    pipeline.set_camera_profile("cam", {"domain": "general", "enabled_events": []})
    assert pipeline._camera_profiles["cam"]["enabled_events"] == []


def test_canonical_profile_passes_through():
    profile = {
        "domain": "construction",
        "enabled_events": ["fire"],
        "zones": {},
        "detection_type": "construction",
    }
    pipeline = SafetyPipeline(detector=Mock())
    pipeline.set_camera_profile("cam", profile)
    stored = pipeline._camera_profiles["cam"]
    assert stored["domain"] == "construction"
    assert stored["enabled_events"] == ["fire"]


def test_legacy_path_emits_deprecation_warning_once(caplog):
    detector = Mock()
    detector.detect.return_value = []
    pipeline = SafetyPipeline(detector=detector)
    assert pipeline._legacy_warned is False
    with caplog.at_level(logging.WARNING, logger="src.pipeline.pipeline"):
        pipeline._process_frame_legacy("cam", Mock(), __import__("datetime").datetime.now())
    assert pipeline._legacy_warned is True
    assert any("legacy" in r.message for r in caplog.records)
