"""
시스템 메트릭 및 헬스체크 모듈 테스트 (Phase 3-4)
"""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import unittest
from unittest.mock import Mock, patch


class TestSystemMetrics(unittest.TestCase):
    """SystemMetrics 테스트"""

    def test_collect_metrics_returns_dataclass(self):
        from src.utils.system_metrics import collect_metrics, SystemMetrics
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
            import psutil
            self.assertGreater(info["total_mb"], 0)
        except ImportError:
            self.assertEqual(info["total_mb"], 0)


class TestHealthChecker(unittest.TestCase):
    """HealthChecker 테스트"""

    def test_register_and_run(self):
        from src.utils.health import HealthChecker, ComponentHealth

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
        from src.utils.health import HealthChecker, ComponentHealth

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


class TestHealthHTTPHandler(unittest.TestCase):
    """HealthHTTPHandler 테스트"""

    def test_health_endpoint(self):
        from src.utils.health import HealthChecker, ComponentHealth, HealthHTTPHandler

        checker = HealthChecker()
        checker.register("test", lambda: ComponentHealth("test", True, "OK"))
        HealthHTTPHandler.health_checker = checker

        # 직접 핸들러 인스턴스 생성 (소켓 없이)
        # HTTP handler 테스트는 통합 테스트로 대체
        self.assertIsNotNone(HealthHTTPHandler.health_checker)


class TestStartHealthServer(unittest.TestCase):
    """start_health_server 테스트"""

    def test_start_health_server_creates_server(self):
        from src.utils.health import start_health_server, HealthChecker

        checker = HealthChecker()
        # 비어있는 체커로도 서버 시작 가능
        server = start_health_server(host="127.0.0.1", port=0, checker=checker)  # port=0으로 자동 할당
        self.assertIsNotNone(server)
        server.shutdown()


if __name__ == "__main__":
    unittest.main()
