"""헤드리스 무인 러너(src/run.py) 단위 테스트.

데몬 종단 기동은 실 모델/카메라가 필요해 수동/통합 검증한다(여기서는 순수 로직 + import).
"""
import os
import sys
import threading
import time
import types
import unittest
from contextlib import ExitStack, contextmanager
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

    def test_shutdown_stops_pipeline_then_outbox(self):
        calls = []
        pipeline = Mock()
        manager = Mock()
        pipeline.stop.side_effect = lambda: calls.append("pipeline")
        manager.shutdown.side_effect = lambda: calls.append("outbox")

        ksi_run._shutdown_runtime(pipeline, manager)

        self.assertEqual(calls, ["pipeline", "outbox"])

    def test_managed_registration_skips_disallowed_target_and_passes_resolution(self):
        class Repo:
            def list(self):
                from src.cameras import CameraConfig
                return 1, [CameraConfig(
                    id="managed", name="Managed", rtsp_url="rtsp://camera/live", resolution=(640, 360),
                    zones=[{
                        "id": "gate", "type": "line", "direction": "positive",
                        "points": [{"x": 0.1, "y": 0.5}, {"x": 0.9, "y": 0.5}],
                    }],
                )]

        class Probe:
            def allowed_host(self, _host, _port):
                return ["192.168.1.20"]

        config = Mock()
        config.load_cameras.return_value = []
        config.get.return_value = 30.0
        pipeline = Mock()
        with patch.object(ksi_run.ConfigLoader, "load", return_value={}):
            self.assertEqual(ksi_run._register_cameras(pipeline, config, Repo(), rtsp_probe=Probe()), 1)
        self.assertEqual(pipeline.add_camera.call_args.kwargs["resize"], (640, 360))
        self.assertIn("192.168.1.20", pipeline.add_camera.call_args.args[1])
        runtime_zone = pipeline.set_camera_profile.call_args.args[1]["zones"]["gate"]
        self.assertEqual(runtime_zone["type"], "line")
        self.assertEqual(runtime_zone["direction"], "positive")

    def test_managed_startup_syncs_enabled_camera_to_gateway(self):
        class Repo:
            def list(self):
                from src.cameras import CameraConfig
                return 1, [CameraConfig(id="managed", name="Managed", rtsp_url="rtsp://camera/live")]

        class Probe:
            def allowed_host(self, _host, _port):
                return ["192.168.1.20"]

        gateway = Mock()
        config = Mock()
        config.load_cameras.return_value = []
        config.get.return_value = 30.0
        with patch.object(ksi_run.ConfigLoader, "load", return_value={}):
            ksi_run._register_cameras(Mock(), config, Repo(), rtsp_probe=Probe(), mediamtx=gateway)
        gateway.sync.assert_called_once()
        self.assertEqual(gateway.sync.call_args.args[0], "managed")
        self.assertIn("192.168.1.20", gateway.sync.call_args.args[1])

    def test_registration_never_starts_more_than_product_limit(self):
        config = Mock()
        config.load_cameras.return_value = [
            {
                "id": f"cam_{index:03d}",
                "enabled": True,
                "stream_url": f"rtsp://192.168.1.{index + 1}/live",
                "fps": 5,
            }
            for index in range(1, 18)
        ]
        config.get.return_value = 30.0
        pipeline = Mock()

        with patch.object(ksi_run.ConfigLoader, "load", return_value={}):
            registered = ksi_run._register_cameras(pipeline, config)

        self.assertEqual(registered, 16)
        self.assertEqual(pipeline.add_camera.call_count, 16)

    def test_gateway_reconciliation_recreates_enabled_paths_and_deletes_disabled(self):
        from src.cameras import CameraConfig

        repository = Mock()
        repository.list.return_value = (2, [
            CameraConfig(id="enabled", name="Enabled", rtsp_url="rtsp://camera/live"),
            CameraConfig(id="disabled", name="Disabled", rtsp_url="rtsp://camera/off", enabled=False),
        ])
        repository.get.side_effect = lambda camera_id: next(
            camera for camera in repository.list.return_value[1] if camera.id == camera_id
        )
        credentials = Mock()
        credentials.get.return_value = {
            "username": "viewer",
            "password": "secret",
            "target_scope": "554|192.168.1.20",
        }
        probe = Mock()
        probe.allowed_host.return_value = ["192.168.1.20"]
        gateway = Mock()

        ksi_run._reconcile_managed_gateway(
            repository,
            credentials,
            probe,
            gateway,
            threading.RLock(),
        )

        gateway.sync.assert_called_once()
        self.assertEqual(gateway.sync.call_args.args[0], "enabled")
        self.assertIn("192.168.1.20", gateway.sync.call_args.args[1])
        gateway.delete.assert_called_once_with("disabled")

    def test_gateway_reconciliation_uses_latest_camera_after_waiting_for_mutation(self):
        from src.cameras import CameraConfig

        snapshot = CameraConfig(id="managed", name="Old", rtsp_url="rtsp://camera/old")
        latest = CameraConfig(id="managed", name="New", rtsp_url="rtsp://camera/new")
        repository = Mock()
        repository.list.return_value = (1, [snapshot])
        repository.get.return_value = latest
        credentials = Mock()
        credentials.get.return_value = None
        probe = Mock()
        probe.allowed_host.return_value = ["192.168.1.20"]
        gateway = Mock()

        ksi_run._reconcile_managed_gateway(
            repository,
            credentials,
            probe,
            gateway,
            threading.RLock(),
        )

        self.assertTrue(gateway.sync.call_args.args[1].endswith("/new"))

    def test_legacy_unscoped_credentials_are_not_used_at_startup_or_reconcile(self):
        from src.cameras import CameraConfig

        camera = CameraConfig(
            id="legacy",
            name="Legacy",
            rtsp_url="rtsp://camera/live",
            credential_ref="camera:legacy",
        )
        repository = Mock()
        repository.list.return_value = (1, [camera])
        repository.get.return_value = camera
        credentials = Mock()
        credentials.get.return_value = {
            "username": "legacy-user",
            "password": "legacy-password",
        }
        probe = Mock()
        probe.allowed_host.return_value = ["192.168.1.20"]
        gateway = Mock()
        config = Mock()
        config.load_cameras.return_value = []
        config.get.return_value = 30.0
        pipeline = Mock()

        with patch.object(ksi_run.ConfigLoader, "load", return_value={}):
            registered = ksi_run._register_cameras(
                pipeline,
                config,
                repository,
                credentials,
                probe,
                gateway,
            )
        ksi_run._reconcile_managed_gateway(
            repository,
            credentials,
            probe,
            gateway,
            threading.RLock(),
        )

        self.assertEqual(registered, 0)
        pipeline.add_camera.assert_not_called()
        gateway.sync.assert_not_called()
        credentials.bind_target.assert_not_called()


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestApiSupervision(unittest.TestCase):
    class _RunningServer:
        def __init__(self, allow_start=None):
            self.started = False
            self.should_exit = False
            self.run_entered = threading.Event()
            self.allow_start = allow_start or threading.Event()
            if allow_start is None:
                self.allow_start.set()

        def run(self):
            self.run_entered.set()
            self.allow_start.wait()
            self.started = True
            while not self.should_exit:
                time.sleep(0.001)

    @contextmanager
    def _patched_main_dependencies(self, server, create_app=None):
        pipeline = Mock()
        alert_manager = Mock()
        config = Mock()
        create_app = create_app or Mock(return_value=Mock())
        uvicorn = types.ModuleType("uvicorn")
        uvicorn.Config = Mock(return_value=Mock())
        uvicorn.Server = Mock(return_value=server)
        api_app = types.ModuleType("src.api.app")
        api_app.create_app = create_app
        notify = Mock()

        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, {"uvicorn": uvicorn, "src.api.app": api_app}))
            stack.enter_context(patch.object(ksi_run.ConfigLoader, "load_env"))
            stack.enter_context(patch.object(ksi_run, "AppConfig", return_value=config))
            stack.enter_context(
                patch.object(ksi_run, "build_pipeline", return_value=(pipeline, alert_manager, Mock()))
            )
            stack.enter_context(patch.object(ksi_run, "CameraRepository", return_value=Mock()))
            stack.enter_context(patch.object(ksi_run, "CredentialStore", return_value=Mock()))
            stack.enter_context(patch.object(ksi_run, "RtspProbe", return_value=Mock()))
            stack.enter_context(patch.object(ksi_run, "_register_cameras", return_value=0))
            stack.enter_context(patch.object(ksi_run.signal, "signal"))
            stack.enter_context(patch.object(ksi_run, "_sd_notify", notify))
            yield pipeline, alert_manager, notify

    def setUp(self):
        ksi_run._shutdown.clear()

    def tearDown(self):
        ksi_run._shutdown.clear()

    def test_api_creation_failure_is_fatal_and_cleans_runtime(self):
        with self._patched_main_dependencies(
            Mock(), create_app=Mock(side_effect=RuntimeError("create failed"))
        ) as (pipeline, alert_manager, _):
            self.assertEqual(ksi_run.main(), 1)

        pipeline.stop.assert_called_once()
        alert_manager.shutdown.assert_called_once()

    def test_api_run_exception_signals_fatal_shutdown(self):
        server = Mock()
        server.run.side_effect = RuntimeError("bind failed")

        thread, failed = ksi_run._start_api_server(server)
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertTrue(failed.is_set())
        self.assertTrue(ksi_run._shutdown.is_set())

    def test_api_clean_return_before_shutdown_signals_fatal_shutdown(self):
        server = Mock()

        thread, failed = ksi_run._start_api_server(server)
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertTrue(failed.is_set())
        self.assertTrue(ksi_run._shutdown.is_set())

    def test_normal_signal_shutdown_is_not_classified_as_api_failure(self):
        server = self._RunningServer()
        pipeline = Mock()
        alert_manager = Mock()
        thread, failed = ksi_run._start_api_server(server)
        self.assertTrue(server.run_entered.wait(timeout=1))

        ksi_run._handle_signal(15, None)
        ksi_run._shutdown_runtime(pipeline, alert_manager, server, thread)

        self.assertTrue(server.should_exit)
        self.assertFalse(thread.is_alive())
        self.assertFalse(failed.is_set())
        pipeline.stop.assert_called_once()
        alert_manager.shutdown.assert_called_once()

    def test_ready_waits_for_live_api_thread(self):
        allow_start = threading.Event()
        server = self._RunningServer(allow_start)
        pipeline_started = threading.Event()
        result = []

        with self._patched_main_dependencies(server) as (pipeline, _, notify):
            pipeline.start.side_effect = pipeline_started.set
            runner = threading.Thread(target=lambda: result.append(ksi_run.main()))
            runner.start()
            self.assertTrue(server.run_entered.wait(timeout=1))
            self.assertFalse(pipeline.start.called)
            self.assertFalse(any(call.args == ("READY=1",) for call in notify.call_args_list))

            allow_start.set()
            self.assertTrue(pipeline_started.wait(timeout=1))
            ksi_run._shutdown.set()
            runner.join(timeout=1)

        self.assertFalse(runner.is_alive())
        self.assertEqual(result, [0])
        self.assertTrue(any(call.args == ("READY=1",) for call in notify.call_args_list))

    def test_unexpected_api_return_exits_main_nonzero_after_cleanup(self):
        class ReturningServer:
            started = True
            should_exit = False

            def run(self):
                return None

        with self._patched_main_dependencies(ReturningServer()) as (pipeline, alert_manager, _):
            self.assertEqual(ksi_run.main(), 1)

        pipeline.stop.assert_called_once()
        alert_manager.shutdown.assert_called_once()


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
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
        # This assembly test retains no evidence; secure runtime defaults require a
        # Fernet key only when capture persistence is intentionally enabled.
        cfg.get_alert_config.return_value = {"save_images": False, "encrypt_images": False}
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

    def test_configured_vlm_intent_survives_provider_creation_failure(self):
        reg = self._fake_registry(active_models=["general_yolo"])
        config = self._fake_cfg()
        vlm = {
            "enabled": True,
            "runtime_qualified": True,
            "model_digest": "a" * 64,
        }
        config.get.side_effect = lambda *keys, default=None: vlm if keys == ("models", "vlm") else {}
        with patch.object(ksi_run.MultiModelRegistry, "from_config", return_value=reg), \
                patch.object(ksi_run.EventPolicyEngine, "from_config", return_value=Mock()), \
                patch.object(ksi_run, "create_vlm_provider", return_value=None):
            pipeline, _, _ = ksi_run.build_pipeline(config)
        runtime = pipeline.get_ai_runtime_status()["vlm"]
        self.assertTrue(runtime["configured_enabled"])
        self.assertTrue(runtime["runtime_qualified"])
        self.assertFalse(runtime["provider_available"])
        self.assertEqual(runtime["reason_category"], "provider_unavailable")

    def test_configured_vlm_concurrency_reaches_pipeline(self):
        reg = self._fake_registry(active_models=["general_yolo"])
        config = self._fake_cfg()
        vlm = {
            "enabled": True,
            "runtime_qualified": True,
            "model_digest": "a" * 64,
            "max_concurrent": 5,
        }
        config.get.side_effect = lambda *keys, default=None: vlm if keys == ("models", "vlm") else {}
        provider = Mock()
        with patch.object(ksi_run.MultiModelRegistry, "from_config", return_value=reg), \
                patch.object(ksi_run.EventPolicyEngine, "from_config", return_value=Mock()), \
                patch.object(ksi_run, "create_vlm_provider", return_value=provider), \
                patch.object(ksi_run.SafetyPipeline, "enable_vlm") as enable_vlm:
            ksi_run.build_pipeline(config)
        enable_vlm.assert_called_once_with(
            provider,
            "./config/prompts/construction.yaml",
            max_concurrent=5,
        )

    def test_appliance_mode_requires_every_enabled_model(self):
        reg = self._fake_registry(active_models=["general_yolo"])
        with patch.dict(os.environ, {"KSI_CCTV_REQUIRE_ALL_MODELS": "1"}), \
                patch.object(ksi_run.MultiModelRegistry, "from_config", return_value=reg) as factory, \
                patch.object(ksi_run.EventPolicyEngine, "from_config", return_value=Mock()):
            ksi_run.build_pipeline(self._fake_cfg())
        self.assertTrue(factory.call_args.kwargs["strict"])

    def test_appliance_mode_requires_cuda_only_model_sessions(self):
        reg = self._fake_registry(active_models=["general_yolo"])
        with patch.dict(os.environ, {"KSI_CCTV_REQUIRE_GPU": "1"}), \
                patch.object(ksi_run.MultiModelRegistry, "from_config", return_value=reg) as factory, \
                patch.object(ksi_run.EventPolicyEngine, "from_config", return_value=Mock()):
            ksi_run.build_pipeline(self._fake_cfg())
        self.assertTrue(factory.call_args.kwargs["require_gpu"])

    def test_runtime_health_does_not_require_a_separate_ollama_service(self):
        config = Mock()
        config.get.return_value = 30.0

        checker = ksi_run._build_health_checker(config, Mock())

        self.assertNotIn("ollama", checker._checks)
        self.assertIn("vlm", checker._checks)


if __name__ == "__main__":
    unittest.main()
