"""헤드리스 무인 러너(src/run.py) 단위 테스트.

데몬 종단 기동은 실 모델/카메라가 필요해 수동/통합 검증한다(여기서는 순수 로직 + import).
"""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    import numpy as np  # noqa: F401  (런타임 의존성 가드용)

    from src import run as ksi_run

    HAS_RUNTIME_DEPS = True
except ModuleNotFoundError:
    HAS_RUNTIME_DEPS = False


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestCameraPolicyProfile(unittest.TestCase):
    """_camera_policy_profile: 헤드리스(대시보드 session_state 없음) 정책 profile 생성."""

    def test_configured_profile_takes_precedence(self):
        camera = {"id": "cam1", "detection_type": "school"}
        profiles = {"cameras": {"cam1": {
            "domain": "factory",
            "enabled_events": ["fire"],
            "zones": {"restricted": {"points": [[0, 0], [1, 1]]}},
        }}}
        p = ksi_run._camera_policy_profile(camera, profiles)
        self.assertEqual(p["domain"], "factory")            # configured가 우선
        self.assertEqual(p["enabled_events"], ["fire"])
        self.assertEqual(p["events"], ["fire"])             # events/enabled_events 동기
        self.assertIn("restricted", p["zones"])
        self.assertEqual(p["detection_type"], "school")     # camera.detection_type

    def test_defaults_when_unconfigured(self):
        p = ksi_run._camera_policy_profile({"id": "cam2"}, {})
        self.assertEqual(p["domain"], "construction")       # 기본 도메인
        self.assertIsNone(p["enabled_events"])              # None=필터없음([]=전부비활성과 구분)
        self.assertEqual(p["zones"], {})
        self.assertEqual(p["detection_type"], "construction")

    def test_detection_type_falls_back_to_domain(self):
        camera = {"id": "cam3"}
        profiles = {"cameras": {"cam3": {"domain": "factory"}}}
        p = ksi_run._camera_policy_profile(camera, profiles)
        self.assertEqual(p["detection_type"], "factory")


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestRunnerModule(unittest.TestCase):
    def test_public_entrypoints_exist(self):
        # 데몬 진입점·조립 헬퍼가 존재(시그니처 회귀 가드)
        self.assertTrue(callable(ksi_run.main))
        self.assertTrue(callable(ksi_run.build_pipeline))
        self.assertTrue(callable(ksi_run._register_cameras))


if __name__ == "__main__":
    unittest.main()
