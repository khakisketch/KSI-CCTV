"""VLM 프롬프트 도메인 선택 회귀 테스트.

내부 소비자는 canonical `domain`을 우선하고, `detection_type`은
하위호환 폴백으로만 사용한다.
"""
import sys
from pathlib import Path
from unittest.mock import Mock

sys.path.append(str(Path(__file__).parent.parent))

from src.pipeline.pipeline import SafetyPipeline


def _make_pipeline():
    return SafetyPipeline(detector=Mock())


def test_domain_only():
    pipeline = _make_pipeline()
    pipeline._camera_profiles["cam"] = {"domain": "factory"}
    assert pipeline._prompt_domain_for_camera("cam") == "factory"


def test_detection_type_only_fallback():
    pipeline = _make_pipeline()
    pipeline._camera_profiles["cam"] = {"detection_type": "construction"}
    assert pipeline._prompt_domain_for_camera("cam") == "construction"


def test_domain_takes_precedence_over_detection_type():
    pipeline = _make_pipeline()
    pipeline._camera_profiles["cam"] = {
        "domain": "factory",
        "detection_type": "construction",
    }
    assert pipeline._prompt_domain_for_camera("cam") == "factory"


def test_missing_or_non_dict_profile_returns_default():
    pipeline = _make_pipeline()
    assert pipeline._prompt_domain_for_camera("unknown") == "default"
    pipeline._camera_profiles["cam"] = "not-a-dict"
    assert pipeline._prompt_domain_for_camera("cam") == "default"
