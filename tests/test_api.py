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


@unittest.skipUnless(_HAS_API, "fastapi 미설치 — skip")
class TestApiWritePrompts(unittest.TestCase):
    """P1b 쓰기 엔드포인트 — open-vocab 프롬프트 설정·모델 토글(인증·검증·안전 갱신)."""

    def _profiles(self, tmp):
        import yaml
        p = Path(tmp) / "model_profiles.yaml"
        p.write_text(
            yaml.safe_dump({"models": {
                "ov": {"type": "open_vocab", "enabled": False, "classes": []},
                "yo": {"type": "yolo_onnx", "enabled": True},
            }}, allow_unicode=True),
            encoding="utf-8",
        )
        return p

    def test_set_prompts_service_cleans_input(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            p = self._profiles(tmp)
            out = service.set_model_prompts(
                ["person", " 지게차 ", "person", ""], model_name="ov", enabled=True, profiles_path=p
            )
            self.assertEqual(out["classes"], ["person", "지게차"])  # trim+dedup+빈값 제거
            self.assertTrue(out["enabled"])
            cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
            self.assertEqual(cfg["models"]["ov"]["classes"], ["person", "지게차"])
            self.assertTrue(cfg["models"]["ov"]["enabled"])

    def test_non_openvocab_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._profiles(tmp)
            with self.assertRaises(ValueError):
                service.set_model_prompts(["x"], model_name="yo", profiles_path=p)

    def test_missing_model_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._profiles(tmp)
            with self.assertRaises(KeyError):
                service.set_model_prompts(["x"], model_name="nope", profiles_path=p)

    def test_empty_prompts_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._profiles(tmp)
            with self.assertRaises(ValueError):
                service.set_model_prompts(["  ", ""], model_name="ov", profiles_path=p)

    def test_set_enabled_service(self):
        import yaml
        with tempfile.TemporaryDirectory() as tmp:
            p = self._profiles(tmp)
            service.set_model_enabled("yo", False, profiles_path=p)
            cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
            self.assertFalse(cfg["models"]["yo"]["enabled"])

    def test_endpoint_requires_auth(self):
        app = create_app()
        client = TestClient(app)
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.post("/api/models/ov/prompts", json={"classes": ["person"]})  # 키 없음
        self.assertEqual(r.status_code, 401)

    def test_endpoint_sets_prompts(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._profiles(tmp)
            with patch.object(service, "_MODEL_PROFILES", p), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ):
                client = TestClient(create_app())
                r = client.post(
                    "/api/models/ov/prompts",
                    headers={"X-API-Key": _KEY},
                    json={"classes": ["fire", "smoke"], "enabled": True},
                )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["classes"], ["fire", "smoke"])

    def test_endpoint_400_for_non_openvocab(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = self._profiles(tmp)
            with patch.object(service, "_MODEL_PROFILES", p), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ):
                client = TestClient(create_app())
                r = client.post(
                    "/api/models/yo/prompts", headers={"X-API-Key": _KEY}, json={"classes": ["x"]}
                )
            self.assertEqual(r.status_code, 400)


@unittest.skipUnless(_HAS_API, "fastapi 미설치 — skip")
class TestApiControl(unittest.TestCase):
    """P1c 제어 엔드포인트 — 라이브 파이프라인 start/stop/status(데몬 mount 시)."""

    def _mock_pipeline(self, running=False):
        from unittest.mock import MagicMock
        pl = MagicMock()
        pl.is_running = running
        pl.get_status.return_value = {"cameras": {}}
        return pl

    def test_status_no_pipeline(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.get("/api/control/status", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["connected"])

    def test_status_with_pipeline(self):
        pl = self._mock_pipeline(running=True)
        client = TestClient(create_app(pipeline=pl))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.get("/api/control/status", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["connected"])
        self.assertTrue(body["running"])

    def test_start_calls_pipeline(self):
        pl = self._mock_pipeline(running=False)
        client = TestClient(create_app(pipeline=pl))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.post("/api/control/start", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        pl.start.assert_called_once()

    def test_stop_calls_pipeline(self):
        pl = self._mock_pipeline(running=True)
        client = TestClient(create_app(pipeline=pl))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.post("/api/control/stop", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 200)
        pl.stop.assert_called_once()

    def test_control_503_without_pipeline(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.post("/api/control/start", headers={"X-API-Key": _KEY})
        self.assertEqual(r.status_code, 503)

    def test_control_requires_auth(self):
        client = TestClient(create_app(pipeline=self._mock_pipeline()))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.post("/api/control/start")  # 키 없음
        self.assertEqual(r.status_code, 401)


@unittest.skipUnless(_HAS_API, "fastapi 미설치 — skip")
class TestApiCompatHealth(unittest.TestCase):
    """compat /health* — 기존 http.server 대체(비인증, 컨테이너/systemd 프로브)."""

    def setUp(self):
        self.client = TestClient(create_app())

    def test_compat_live_unauth_200(self):
        r = self.client.get("/health/live")  # 비인증
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "alive")

    def test_compat_health_unauth(self):
        r = self.client.get("/health")  # 비인증, disk/memory 정상이면 200, 아니면 503
        self.assertIn(r.status_code, (200, 503))
        self.assertIn("components", r.json())

    def test_compat_ready_unauth(self):
        r = self.client.get("/health/ready")
        self.assertIn(r.status_code, (200, 503))
        self.assertIn("status", r.json())


if __name__ == "__main__":
    unittest.main()
