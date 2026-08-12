"""데모/시연용 ASGI 앱(demo_app.py) 테스트.

src/api/demo_app.py는 모듈 로드 시점에 이미 _DemoPipeline()이 주입된 FastAPI app을
노출하므로, TestClient에 해당 app을 그대로 넘겨 텔레메트리·제어 흐름을 검증한다.
기존 tests/test_api.py의 인증/제어/텔레메트리 테스트 패턴을 재사용한다.
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

try:
    from fastapi.testclient import TestClient

    from src.api.demo_app import app

    _HAS_API = True
except Exception:
    _HAS_API = False

_KEY = "demo-test-api-key"


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestDemoApp(unittest.TestCase):
    """데모 앱 헬스·텔레메트리·파이프라인 제어 통합 검증."""

    def setUp(self):
        self.client = TestClient(app)
        self.h = {"X-API-Key": _KEY}

    def _get(self, path):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            return self.client.get(path, headers=self.h)

    def _post(self, path):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            return self.client.post(path, headers=self.h)

    def test_health_live_unauthenticated(self):
        """/health/live는 비인증으로 alive 상태를 반환해야 한다."""
        r = self.client.get("/health/live")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "alive")

    def test_camera_telemetry_synthetic(self):
        """데모 파이프라인의 합성 텔레메트리가 cam_001/cam_002를 노출한다."""
        r = self._get("/api/cameras/telemetry")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["connected"])
        self.assertIn("cam_001", body["cameras"])
        self.assertIn("cam_002", body["cameras"])
        cam1 = body["cameras"]["cam_001"]
        cam2 = body["cameras"]["cam_002"]
        self.assertTrue(cam1["connected"])
        self.assertEqual(cam1["fps"], 24.9)
        self.assertFalse(cam1["is_stale"])
        self.assertFalse(cam2["connected"])
        self.assertTrue(cam2["is_stale"])

    def test_control_status_initial_running(self):
        """데모 파이프라인 초기 상태는 running=True."""
        r = self._get("/api/control/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["connected"])
        self.assertTrue(body["running"])

    def test_control_stop_then_status(self):
        """POST /api/control/stop 후 running=False로 전환된다."""
        r = self._post("/api/control/stop")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["running"])

        r2 = self._get("/api/control/status")
        self.assertEqual(r2.status_code, 200)
        self.assertFalse(r2.json()["running"])

    def test_control_start_then_status(self):
        """stop 상태에서 POST /api/control/start 시 running=True로 복귀한다."""
        self._post("/api/control/stop")
        r = self._post("/api/control/start")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["running"])

        r2 = self._get("/api/control/status")
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["running"])

    def test_telemetry_requires_auth(self):
        """텔레메트리는 API 키 없이 조회할 수 없어야 한다."""
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = self.client.get("/api/cameras/telemetry")
        self.assertEqual(r.status_code, 401)

    def test_control_requires_auth(self):
        """제어 엔드포인트는 API 키 없이 호출할 수 없어야 한다."""
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = self.client.post("/api/control/stop")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
