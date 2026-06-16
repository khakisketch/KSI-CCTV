"""헤드리스 무인 러너(src/run.py) 단위 테스트.

데몬 종단 기동은 실 모델/카메라가 필요해 수동/통합 검증한다(여기서는 순수 로직 + import).
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

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


class TestSdNotify(unittest.TestCase):
    """systemd sd_notify(watchdog) — NOTIFY_SOCKET 유무에 따른 동작."""

    def test_noop_without_notify_socket(self):
        """NOTIFY_SOCKET 미설정(비-systemd)이면 예외 없이 no-op."""
        import os
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("NOTIFY_SOCKET", None)
            ksi_run._sd_notify("READY=1")  # 예외 없으면 성공

    def test_sends_datagram_when_socket_set(self):
        """NOTIFY_SOCKET이 설정되면 해당 유닉스 DGRAM 소켓으로 상태를 전송한다."""
        import os
        import socket
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "notify.sock")
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            srv.bind(path)
            srv.settimeout(2)
            try:
                with patch.dict(os.environ, {"NOTIFY_SOCKET": path}):
                    ksi_run._sd_notify("WATCHDOG=1")
                self.assertEqual(srv.recv(64), b"WATCHDOG=1")
            finally:
                srv.close()


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestBuildPipeline(unittest.TestCase):
    """build_pipeline 조립 로직 — mock registry/engine으로 행사(실 모델 불필요)."""

    def _fake_cfg(self):
        cfg = Mock()
        cfg.get_detection_config.return_value = {}
        cfg.get_alert_config.return_value = {}
        cfg.get.return_value = {}  # models.vlm → {} → VLM 비활성
        return cfg

    def _fake_registry(self, active_models):
        reg = Mock()
        reg.active_model_names = active_models
        reg.failed_models = {}
        detector = Mock(classes={0: "person"})
        reg.get_model.return_value = detector
        reg.models = {"general_yolo": detector}
        return reg

    def test_raises_when_no_active_models(self):
        reg = self._fake_registry(active_models=[])
        with patch.object(ksi_run.MultiModelRegistry, "from_config", return_value=reg), \
             patch.object(ksi_run.EventPolicyEngine, "from_config", return_value=Mock()), \
             self.assertRaises(RuntimeError):
            ksi_run.build_pipeline(self._fake_cfg())

    def test_builds_with_active_models_and_vlm_disabled(self):
        reg = self._fake_registry(active_models=["general_yolo"])
        with patch.object(ksi_run.MultiModelRegistry, "from_config", return_value=reg), \
             patch.object(ksi_run.EventPolicyEngine, "from_config", return_value=Mock()):
            pipeline, alert_manager, registry = ksi_run.build_pipeline(self._fake_cfg())
        self.assertIsNotNone(pipeline)
        self.assertIsNotNone(alert_manager)
        self.assertIs(registry, reg)
        # VLM cfg가 {} → provider 미주입(선택적 컴포넌트)
        self.assertIsNone(getattr(pipeline, "vlm_provider", None))


if __name__ == "__main__":
    unittest.main()
