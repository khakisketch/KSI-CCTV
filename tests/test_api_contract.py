"""API 계약 고정 회귀 테스트 (U5).

- 무주입 standalone 모드는 degraded로 명시된다.
- compat /health*는 키 없이 200 (의도적 공개), /api/*는 fail-closed를 유지한다.
"""
import os
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.append(str(Path(__file__).parent.parent))

try:
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    HAS_API = True
except ImportError:
    HAS_API = False

import unittest


@unittest.skipUnless(HAS_API, "fastapi 미설치 — skip")
class TestApiContract(unittest.TestCase):
    def test_standalone_mode_is_declared_degraded(self):
        app = create_app()
        self.assertTrue(app.state.standalone_degraded)

    def test_fully_wired_mode_is_not_degraded(self):
        app = create_app(
            event_repository=Mock(),
            alert_manager=Mock(),
            incident_store=Mock(),
        )
        self.assertFalse(app.state.standalone_degraded)

    def test_compat_health_endpoints_are_public(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KSI_CCTV_API_KEY", None)
            for path in ("/health", "/healthz", "/health/ready"):
                r = client.get(path)
                self.assertEqual(r.status_code, 200, path)

    def test_protected_api_stays_fail_closed_without_key(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KSI_CCTV_API_KEY", None)
            r = client.get("/api/cameras")
        self.assertEqual(r.status_code, 503)
