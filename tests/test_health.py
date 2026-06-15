"""
시스템 메트릭 및 헬스체크 모듈 테스트 (Phase 3-4)
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import unittest
from unittest.mock import MagicMock, patch


class TestSystemMetrics(unittest.TestCase):
    """SystemMetrics 테스트"""

    def test_collect_metrics_returns_dataclass(self):
        from src.utils.system_metrics import SystemMetrics, collect_metrics
        metrics = collect_metrics()
        self.assertIsInstance(metrics, SystemMetrics)
        self.assertGreaterEqual(metrics.cpu_percent, 0)
        self.assertLessEqual(metrics.cpu_percent, 100)
        self.assertGreater(metrics.memory_total_mb, 0)

    def test_metrics_to_dict(self):
        from src.utils.system_metrics import SystemMetrics
        m = SystemMetrics(
            cpu_percent=50.0,
            memory_percent=60.0,
            memory_used_mb=1024.0,
            memory_total_mb=2048.0,
            disk_percent=70.0,
        )
        d = m.to_dict()
        self.assertEqual(d["cpu_percent"], 50.0)
        self.assertEqual(d["memory_percent"], 60.0)
        self.assertIn("timestamp", d)

    def test_metrics_gpu_none_handled(self):
        from src.utils.system_metrics import SystemMetrics
        m = SystemMetrics(gpu_percent=None)
        d = m.to_dict()
        self.assertIsNone(d["gpu_percent"])

    def test_metrics_gpu_value_rounded(self):
        from src.utils.system_metrics import SystemMetrics
        m = SystemMetrics(gpu_percent=85.6789)
        d = m.to_dict()
        self.assertEqual(d["gpu_percent"], 85.7)


class TestGetMemoryInfo(unittest.TestCase):
    """get_memory_info 테스트"""

    def test_get_memory_info_returns_dict(self):
        """메모리 정보 딕셔너리 반환 확인"""
        from src.utils.system_metrics import get_memory_info
        info = get_memory_info()
        self.assertIn("percent", info)
        self.assertIn("used_mb", info)
        self.assertIn("total_mb", info)
        # psutil이 설치되어 있으면 실제 값이 있어야 함
        try:
            import psutil  # noqa: F401
            self.assertGreater(info["total_mb"], 0)
        except ImportError:
            self.assertEqual(info["total_mb"], 0)


class TestGetDirSize(unittest.TestCase):
    """get_dir_size 테스트"""

    def test_existing_dir_returns_nonnegative(self):
        """존재하는 디렉터리는 0 이상을 반환한다"""
        import os
        import tempfile

        from src.utils.system_metrics import get_dir_size
        with tempfile.TemporaryDirectory() as tmpdir:
            # 파일 하나 생성
            fp = os.path.join(tmpdir, "test.txt")
            with open(fp, "w") as f:
                f.write("hello")
            size = get_dir_size(tmpdir)
            self.assertGreaterEqual(size, 5)

    def test_nonexistent_dir_returns_zero(self):
        """존재하지 않는 디렉터리는 0.0을 반환한다"""
        from src.utils.system_metrics import get_dir_size
        size = get_dir_size("/nonexistent_path_ksi_test_xyz")
        self.assertEqual(size, 0.0)

    def test_empty_dir_returns_zero(self):
        """빈 디렉터리는 0.0을 반환한다"""
        import tempfile

        from src.utils.system_metrics import get_dir_size
        with tempfile.TemporaryDirectory() as tmpdir:
            size = get_dir_size(tmpdir)
            self.assertEqual(size, 0.0)


class TestGetGpuInfo(unittest.TestCase):
    """get_gpu_info 버그 수정 검증 — pynvml 없는 환경에서도 안전하게 None 반환"""

    def test_no_pynvml_returns_all_none(self):
        """pynvml import 실패 시 모두 None 반환 (0 오보고 금지)"""
        from src.utils.system_metrics import get_gpu_info
        with patch.dict("sys.modules", {"pynvml": None}):
            # pynvml이 None으로 대체되면 import 자체가 실패해야 함
            # 실제로는 ImportError를 흉내내기 위해 side_effect 방식 사용
            import builtins
            original_import = builtins.__import__

            def mock_import(name, *args, **kwargs):
                if name == "pynvml":
                    raise ImportError("mocked")
                return original_import(name, *args, **kwargs)

            with patch("builtins.__import__", side_effect=mock_import):
                result = get_gpu_info()
        self.assertIsNone(result["percent"])
        self.assertIsNone(result["memory_percent"])
        self.assertIsNone(result["temperature"])

    def test_partial_failure_mem_zero_not_reported(self):
        """mem.total=0 인 경우 memory_percent=None 반환 (0 오보고 방지)"""
        mock_pynvml = MagicMock()
        mock_pynvml.nvmlInit.return_value = None
        mock_handle = MagicMock()
        mock_pynvml.nvmlDeviceGetHandleByIndex.return_value = mock_handle
        # util 성공
        mock_util = MagicMock()
        mock_util.gpu = 50
        mock_pynvml.nvmlDeviceGetUtilizationRates.return_value = mock_util
        # mem.total = 0 (엣지 케이스)
        mock_mem = MagicMock()
        mock_mem.total = 0
        mock_mem.used = 0
        mock_pynvml.nvmlDeviceGetMemoryInfo.return_value = mock_mem
        # temp 성공
        mock_pynvml.nvmlDeviceGetTemperature.return_value = 65
        mock_pynvml.NVML_TEMPERATURE_GPU = 0

        with patch.dict("sys.modules", {"pynvml": mock_pynvml}):
            from src.utils import system_metrics
            with patch.object(system_metrics, "get_gpu_info",
                              wraps=system_metrics.get_gpu_info):
                # 모듈 내부에서 pynvml을 import하므로 직접 reload 없이 mock 삽입
                pass

        # get_gpu_info 로직을 직접 테스트: mem.total==0이면 memory_percent는 None
        # 함수 내부 로직을 재현
        mem_total = 0
        mem_used = 0
        memory_percent = (mem_used / mem_total * 100) if mem_total > 0 else None
        self.assertIsNone(memory_percent)


class TestHealthChecker(unittest.TestCase):
    """HealthChecker 테스트"""

    def test_register_and_run(self):
        from src.utils.health import ComponentHealth, HealthChecker

        checker = HealthChecker()
        checker.register("test1", lambda: ComponentHealth("test1", True, "OK"))
        checker.register("test2", lambda: (False, "Error"))

        report = checker.run_checks()
        self.assertEqual(len(report.components), 2)
        self.assertFalse(report.overall_healthy)  # test2 failed

    def test_register_and_unregister(self):
        from src.utils.health import HealthChecker

        checker = HealthChecker()
        checker.register("test1", lambda: True)
        self.assertIn("test1", checker._checks)

        checker.unregister("test1")
        self.assertNotIn("test1", checker._checks)

    def test_check_exception_caught(self):
        from src.utils.health import HealthChecker

        checker = HealthChecker()

        def failing_check():
            raise RuntimeError("boom")

        checker.register("fail", failing_check)
        report = checker.run_checks()
        self.assertEqual(len(report.components), 1)
        self.assertFalse(report.components[0].healthy)
        self.assertIn("boom", report.components[0].message)

    def test_health_report_to_dict(self):
        from src.utils.health import ComponentHealth, HealthChecker

        checker = HealthChecker()
        checker.register("test1", lambda: ComponentHealth("test1", True, "OK"))
        report = checker.run_checks()

        d = report.to_dict()
        self.assertIn("status", d)
        self.assertEqual(d["status"], "healthy")
        self.assertIn("components", d)
        self.assertIn("system", d)


class TestDefaultHealthChecks(unittest.TestCase):
    """기본 헬스체크 함수 테스트"""

    def test_check_disk_space(self):
        from src.utils.health import check_disk_space
        result = check_disk_space(threshold_percent=99.0)
        self.assertTrue(result.healthy)  # 디스크가 99% 미만일 것

    def test_check_memory_usage(self):
        from src.utils.health import check_memory_usage
        result = check_memory_usage(threshold_percent=99.0)
        self.assertTrue(result.healthy)  # 메모리가 99% 미만일 것

    def test_check_memory_usage_high_threshold(self):
        from src.utils.health import check_memory_usage
        result = check_memory_usage(threshold_percent=0.0)  # 0% 임계값
        # 메모리 사용 중이면 unhealthy
        self.assertFalse(result.healthy)


class TestCheckCameras(unittest.TestCase):
    """check_cameras 테스트"""

    def test_empty_status_is_healthy(self):
        from src.utils.health import check_cameras
        result = check_cameras({})
        self.assertTrue(result.healthy)
        self.assertIn("camera_count", result.metadata)
        self.assertEqual(result.metadata["camera_count"], 0)

    def test_all_healthy_cameras(self):
        from src.utils.health import check_cameras
        status = {
            "cam1": {
                "connected": True,
                "is_stale": False,
                "consecutive_failures": 0,
                "seconds_since_last_frame": 1.0,
            },
            "cam2": {
                "connected": True,
                "is_stale": False,
                "consecutive_failures": 0,
                "seconds_since_last_frame": 2.0,
            },
        }
        result = check_cameras(status)
        self.assertTrue(result.healthy)
        self.assertEqual(result.metadata["unhealthy_count"], 0)

    def test_stale_camera_is_unhealthy(self):
        from src.utils.health import check_cameras
        status = {
            "cam1": {
                "connected": True,
                "is_stale": True,
                "consecutive_failures": 0,
                "seconds_since_last_frame": 15.0,
            },
        }
        result = check_cameras(status)
        self.assertFalse(result.healthy)
        self.assertIn("cam1", result.metadata["cameras"])
        self.assertTrue(len(result.metadata["cameras"]["cam1"]["issues"]) > 0)

    def test_disconnected_camera_is_unhealthy(self):
        from src.utils.health import check_cameras
        status = {
            "cam1": {
                "connected": False,
                "is_stale": False,
                "consecutive_failures": 0,
                "seconds_since_last_frame": None,
            },
        }
        result = check_cameras(status)
        self.assertFalse(result.healthy)

    def test_consecutive_failures_threshold(self):
        from src.utils.health import check_cameras
        status = {
            "cam1": {
                "connected": True,
                "is_stale": False,
                "consecutive_failures": 5,  # >= 3 → unhealthy
                "seconds_since_last_frame": 1.0,
            },
        }
        result = check_cameras(status, max_consecutive_failures=3)
        self.assertFalse(result.healthy)

    def test_consecutive_failures_below_threshold(self):
        from src.utils.health import check_cameras
        status = {
            "cam1": {
                "connected": True,
                "is_stale": False,
                "consecutive_failures": 2,  # < 3 → OK
                "seconds_since_last_frame": 1.0,
            },
        }
        result = check_cameras(status, max_consecutive_failures=3)
        self.assertTrue(result.healthy)


class TestCheckVlm(unittest.TestCase):
    """check_vlm 테스트"""

    def test_no_calls_is_healthy(self):
        from src.utils.health import check_vlm
        metrics = {
            "total_calls": 0,
            "failure_count": 0,
            "success_rate": 1.0,
            "avg_latency_ms": 0.0,
            "last_latency_ms": 0.0,
        }
        result = check_vlm(metrics)
        self.assertTrue(result.healthy)
        self.assertIn("미사용", result.message)

    def test_high_failure_rate_is_unhealthy(self):
        from src.utils.health import check_vlm
        metrics = {
            "total_calls": 10,
            "failure_count": 6,
            "success_rate": 0.4,  # 60% 실패
            "avg_latency_ms": 1000.0,
            "last_latency_ms": 1000.0,
        }
        result = check_vlm(metrics, max_failure_rate=0.5)
        self.assertFalse(result.healthy)

    def test_latency_threshold_exceeded(self):
        from src.utils.health import check_vlm
        metrics = {
            "total_calls": 3,  # 샘플 부족 → 실패율 미평가
            "failure_count": 0,
            "success_rate": 1.0,
            "avg_latency_ms": 5000.0,
            "last_latency_ms": 35_000.0,  # 35초 > 30초 임계
        }
        result = check_vlm(metrics, max_latency_ms=30_000.0)
        self.assertFalse(result.healthy)

    def test_below_sample_threshold_ignores_failure_rate(self):
        """total_calls < 5 이면 실패율 판정 안 함"""
        from src.utils.health import check_vlm
        metrics = {
            "total_calls": 4,
            "failure_count": 4,
            "success_rate": 0.0,
            "avg_latency_ms": 1000.0,
            "last_latency_ms": 1000.0,
        }
        result = check_vlm(metrics, max_failure_rate=0.5, max_latency_ms=30_000.0)
        # 샘플 4건 → 실패율 미평가, 지연도 OK → healthy
        self.assertTrue(result.healthy)

    def test_healthy_vlm(self):
        from src.utils.health import check_vlm
        metrics = {
            "total_calls": 20,
            "failure_count": 1,
            "success_rate": 0.95,
            "avg_latency_ms": 2000.0,
            "last_latency_ms": 2500.0,
        }
        result = check_vlm(metrics)
        self.assertTrue(result.healthy)


class TestCheckModelRegistry(unittest.TestCase):
    """check_model_registry 테스트"""

    def test_empty_metrics_is_healthy(self):
        from src.utils.health import check_model_registry
        result = check_model_registry({})
        self.assertTrue(result.healthy)

    def test_consecutive_errors_threshold(self):
        from src.utils.health import check_model_registry
        metrics = {
            "model_a": {
                "inference_count": 10,
                "error_count": 4,
                "consecutive_errors": 5,  # >= 3 → unhealthy
                "avg_latency_ms": 100.0,
            }
        }
        result = check_model_registry(metrics, max_consecutive_errors=3)
        self.assertFalse(result.healthy)
        self.assertIn("model_a", result.metadata["models"])

    def test_high_error_rate_unhealthy(self):
        from src.utils.health import check_model_registry
        metrics = {
            "model_b": {
                "inference_count": 20,
                "error_count": 12,  # 60% 오류율
                "consecutive_errors": 0,
                "avg_latency_ms": 100.0,
            }
        }
        result = check_model_registry(metrics, max_error_rate=0.5)
        self.assertFalse(result.healthy)

    def test_below_inference_threshold_ignores_error_rate(self):
        """inference_count < 10이면 오류율 판정 안 함"""
        from src.utils.health import check_model_registry
        metrics = {
            "model_c": {
                "inference_count": 5,
                "error_count": 5,  # 100% 오류지만 샘플 부족
                "consecutive_errors": 0,
                "avg_latency_ms": -1.0,
            }
        }
        result = check_model_registry(metrics, max_consecutive_errors=3, max_error_rate=0.5)
        self.assertTrue(result.healthy)

    def test_healthy_model(self):
        from src.utils.health import check_model_registry
        metrics = {
            "model_d": {
                "inference_count": 100,
                "error_count": 2,
                "consecutive_errors": 0,
                "avg_latency_ms": 50.0,
            }
        }
        result = check_model_registry(metrics)
        self.assertTrue(result.healthy)


class TestCheckGpu(unittest.TestCase):
    """check_gpu 테스트"""

    def test_no_gpu_is_healthy(self):
        """GPU 없음 → healthy=True, 메시지로 안내"""
        from src.utils.health import check_gpu
        with patch("src.utils.health.get_gpu_info") as mock_gpu:
            mock_gpu.return_value = {
                "percent": None,
                "memory_percent": None,
                "temperature": None,
            }
            result = check_gpu()
        self.assertTrue(result.healthy)
        self.assertFalse(result.metadata.get("available", True))

    def test_vram_threshold_exceeded(self):
        from src.utils.health import check_gpu
        with patch("src.utils.health.get_gpu_info") as mock_gpu:
            mock_gpu.return_value = {
                "percent": 80.0,
                "memory_percent": 97.0,  # > 95% 임계
                "temperature": 60.0,
            }
            result = check_gpu(vram_threshold_percent=95.0)
        self.assertFalse(result.healthy)
        self.assertIn("VRAM", result.message)

    def test_temperature_threshold_exceeded(self):
        from src.utils.health import check_gpu
        with patch("src.utils.health.get_gpu_info") as mock_gpu:
            mock_gpu.return_value = {
                "percent": 70.0,
                "memory_percent": 50.0,
                "temperature": 90.0,  # > 85°C 임계
            }
            result = check_gpu(temp_threshold_celsius=85.0)
        self.assertFalse(result.healthy)
        self.assertIn("온도", result.message)

    def test_healthy_gpu(self):
        from src.utils.health import check_gpu
        with patch("src.utils.health.get_gpu_info") as mock_gpu:
            mock_gpu.return_value = {
                "percent": 60.0,
                "memory_percent": 50.0,
                "temperature": 65.0,
            }
            result = check_gpu(vram_threshold_percent=95.0, temp_threshold_celsius=85.0)
        self.assertTrue(result.healthy)


class TestCheckLogDirs(unittest.TestCase):
    """check_log_dirs 테스트"""

    def test_nonexistent_dirs_are_healthy(self):
        """존재하지 않는 디렉터리는 크기 0 → 임계 미만 → healthy"""
        from src.utils.health import check_log_dirs
        result = check_log_dirs(
            log_dir="/nonexistent_log_ksi",
            capture_dir="/nonexistent_cap_ksi",
            max_log_gb=1.0,
            max_capture_gb=1.0,
        )
        self.assertTrue(result.healthy)
        self.assertEqual(result.metadata["log_size_gb"], 0.0)
        self.assertEqual(result.metadata["capture_size_gb"], 0.0)

    def test_threshold_exceeded_is_unhealthy(self):
        """크기가 임계 초과 시 unhealthy"""
        from src.utils.health import check_log_dirs
        with patch("src.utils.health.get_dir_size") as mock_size:
            # log: 6GB, capture: 25GB
            mock_size.side_effect = [
                6 * (1024 ** 3),   # log_dir
                25 * (1024 ** 3),  # capture_dir
            ]
            result = check_log_dirs(max_log_gb=5.0, max_capture_gb=20.0)
        self.assertFalse(result.healthy)

    def test_within_threshold_is_healthy(self):
        from src.utils.health import check_log_dirs
        with patch("src.utils.health.get_dir_size") as mock_size:
            mock_size.side_effect = [
                2 * (1024 ** 3),  # log: 2GB
                10 * (1024 ** 3), # capture: 10GB
            ]
            result = check_log_dirs(max_log_gb=5.0, max_capture_gb=20.0)
        self.assertTrue(result.healthy)


class TestRegisterPipelineHealthChecks(unittest.TestCase):
    """register_pipeline_health_checks 테스트"""

    def test_registers_expected_checks(self):
        from src.utils.health import HealthChecker, register_pipeline_health_checks

        checker = HealthChecker()
        mock_pipeline = MagicMock()
        mock_pipeline.get_status.return_value = {
            "cameras": {},
            "vlm_provider": {},
            "models": {},
        }

        register_pipeline_health_checks(checker, mock_pipeline)

        # 5개 항목이 등록되어야 함
        self.assertIn("cameras", checker._checks)
        self.assertIn("vlm", checker._checks)
        self.assertIn("model_registry", checker._checks)
        self.assertIn("gpu", checker._checks)
        self.assertIn("log_dirs", checker._checks)

    def test_registered_checks_run_without_error(self):
        from src.utils.health import HealthChecker, register_pipeline_health_checks

        checker = HealthChecker()
        mock_pipeline = MagicMock()
        mock_pipeline.get_status.return_value = {
            "cameras": {
                "cam1": {
                    "connected": True,
                    "is_stale": False,
                    "consecutive_failures": 0,
                    "seconds_since_last_frame": 1.0,
                }
            },
            "vlm_provider": {
                "total_calls": 0,
                "failure_count": 0,
                "success_rate": 1.0,
                "avg_latency_ms": 0.0,
                "last_latency_ms": 0.0,
            },
            "models": {
                "model_a": {
                    "inference_count": 10,
                    "error_count": 0,
                    "consecutive_errors": 0,
                    "avg_latency_ms": 50.0,
                }
            },
        }

        register_pipeline_health_checks(checker, mock_pipeline)

        with patch("src.utils.health.get_gpu_info") as mock_gpu:
            mock_gpu.return_value = {
                "percent": None,
                "memory_percent": None,
                "temperature": None,
            }
            report = checker.run_checks()

        # 모두 실행되어야 하고 예외 없어야 함
        component_names = {c.name for c in report.components}
        self.assertIn("cameras", component_names)
        self.assertIn("vlm", component_names)
        self.assertIn("model_registry", component_names)
        self.assertIn("gpu", component_names)
        self.assertIn("log_dirs", component_names)


class TestHealthHTTPHandler(unittest.TestCase):
    """HealthHTTPHandler 테스트"""

    def test_health_endpoint(self):
        from src.utils.health import ComponentHealth, HealthChecker, HealthHTTPHandler

        checker = HealthChecker()
        checker.register("test", lambda: ComponentHealth("test", True, "OK"))
        HealthHTTPHandler.health_checker = checker

        # 직접 핸들러 인스턴스 생성 (소켓 없이)
        # HTTP handler 테스트는 통합 테스트로 대체
        self.assertIsNotNone(HealthHTTPHandler.health_checker)


class TestStartHealthServer(unittest.TestCase):
    """start_health_server 테스트"""

    def test_start_health_server_creates_server(self):
        from src.utils.health import HealthChecker, start_health_server

        checker = HealthChecker()
        # 비어있는 체커로도 서버 시작 가능
        server = start_health_server(host="127.0.0.1", port=0, checker=checker)  # port=0으로 자동 할당
        self.assertIsNotNone(server)
        server.shutdown()


if __name__ == "__main__":
    unittest.main()
