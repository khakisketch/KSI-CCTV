"""설정 SSOT 소유권 회귀 테스트 (U1). 행위 변경 없이 문서 포인터를 고정한다."""
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _read(name: str) -> str:
    return (REPO / name).read_text(encoding="utf-8")


def test_default_yaml_points_to_model_profiles_ssot():
    assert "config/model_profiles.yaml" in _read("config/default.yaml")


def test_default_yaml_points_to_event_policies_ssot():
    assert "config/event_policies.yaml" in _read("config/default.yaml")


def test_default_yaml_camera_source_matches_loader():
    text = _read("config/default.yaml")
    assert "config/cameras.json" in text
    assert "config/cameras.yaml에서 관리" not in text


def test_detection_target_classes_marked_legacy():
    assert "legacy" in _read("config/default.yaml")
