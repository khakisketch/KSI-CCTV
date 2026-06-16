"""엔진 API(FastAPI) 테스트 — 인증 fail-closed, 시크릿 마스킹, 엔드포인트 스키마."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

try:
    from fastapi.testclient import TestClient

    from src.api import service
    from src.api.app import create_app
    _HAS_API = True
except Exception:
    _HAS_API = False

_KEY = "test-api-key-strong"


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestApiAuth(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)

    def test_health_live_is_public(self):
        r = self.client.get("/api/health/live")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "alive")

    def test_protected_fail_closed_when_key_unset(self):
        """KSI_CCTV_API_KEY 미설정 → 보호 엔드포인트 503(fail-closed)."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KSI_CCTV_API_KEY", None)
            r = self.client.get("/api/models")
        self.assertEqual(r.status_code, 503)

    def test_protected_rejects_wrong_key(self):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = self.client.get("/api/models", headers={"X-API-Key": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_protected_accepts_correct_key(self):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = self.client.get("/api/models", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestApiEndpoints(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.client = TestClient(self.app)
        self.h = {"X-API-Key": _KEY}

    def _get(self, path):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            return self.client.get(path, headers=self.h)

    def test_models_endpoint(self):
        r = self._get("/api/models")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("models", body)
        names = {m["name"] for m in body["models"]}
        self.assertIn("general_yolo", names)  # 기본 모델 존재

    def test_policies_endpoint_has_general(self):
        r = self._get("/api/policies")
        self.assertEqual(r.status_code, 200)
        self.assertIn("general", r.json()["profiles"])  # 범용 프로파일 노출

    def test_cameras_endpoint(self):
        r = self._get("/api/cameras")
        self.assertEqual(r.status_code, 200)
        self.assertIn("cameras", r.json())

    def test_config_endpoint_no_raw_secrets(self):
        r = self._get("/api/config")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("detection", body)
        self.assertIn("vlm", body)
        # base_url에 평문 자격증명이 노출되지 않아야 함(마스킹 뷰)
        self.assertNotIn("password", str(body).lower())

    def test_events_endpoint_shape(self):
        r = self._get("/api/events?limit=5")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("events", body)
        self.assertIsInstance(body["events"], list)


@unittest.skipUnless(_HAS_API, "fastapi 미설치 — skip")
class TestServiceMaskingAndEvents(unittest.TestCase):
    def test_events_mask_rtsp_in_description(self):
        """alerts.log의 description에 든 RTSP 자격증명이 마스킹돼 반환된다."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                '2026-06-16 10:00:00 | INFO | EVENT | '
                '{"event_type":"person_detected","camera_id":"cam1",'
                '"description":"rtsp://admin:secret123@10.0.0.5/s"}\n',
                encoding="utf-8",
            )
            events = service.list_events(limit=10, alerts_log=log)
        self.assertEqual(len(events), 1)
        self.assertNotIn("secret123", events[0]["description"])

    def test_events_filter_and_reverse(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            lines = [
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_type":"fire","camera_id":"a"}',
                '2026-06-16 10:00:01 | INFO | EVENT | {"event_type":"person_detected","camera_id":"a"}',
                '2026-06-16 10:00:02 | INFO | EVENT | {"event_type":"fire","camera_id":"b"}',
            ]
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            fires = service.list_events(limit=10, event_type="fire", alerts_log=log)
        self.assertEqual([e["camera_id"] for e in fires], ["b", "a"])  # 최신순

    def test_cameras_masks_stream_url(self):
        class _Cfg:
            def load_cameras(self):
                return [{"id": "c1", "name": "n", "enabled": True,
                         "stream_url": "rtsp://user:pw@10.0.0.9:554/live"}]
        cams = service.list_cameras(_Cfg())
        self.assertNotIn("pw", cams[0]["stream_url"])


if __name__ == "__main__":
    unittest.main()
