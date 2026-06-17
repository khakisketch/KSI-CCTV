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

# 쓰기 엔드포인트(업로드/프롬프트/enabled/제어)는 이제 감사 로그를 남긴다. 테스트가 실제
# logs/dashboard_audit.jsonl을 오염시키지 않도록, 모듈 전역으로 _AUDIT_LOG를 임시 경로로 돌린다.
_audit_redirect = None


def setUpModule():
    global _audit_redirect
    if not _HAS_API:
        return
    _audit_redirect = tempfile.TemporaryDirectory()
    service._AUDIT_LOG = Path(_audit_redirect.name) / "audit.jsonl"


def tearDownModule():
    if _audit_redirect is not None:
        _audit_redirect.cleanup()


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


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestEventStreamSSE(unittest.IsolatedAsyncioTestCase):
    async def test_stream_pushes_new_event_only(self):
        """stream_events는 파일 끝 이후의 신규 라인만 SSE 프레임으로 push한다(초기 라인 skip)."""
        import asyncio

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            # 초기 라인(이건 push되면 안 됨 — tail은 끝부터)
            log.write_text(
                '2026-06-17 10:00:00 | INFO | EVENT | {"event_type":"old","camera_id":"a"}\n',
                encoding="utf-8",
            )

            async def never_disconnected() -> bool:
                return False

            gen = service.stream_events(
                never_disconnected, alerts_log=log, poll_interval=0.02, heartbeat_every=10_000
            )

            async def appender() -> None:
                await asyncio.sleep(0.1)  # 제너레이터가 pos를 끝으로 잡은 뒤 추가
                with open(log, "a", encoding="utf-8") as f:
                    f.write(
                        '2026-06-17 10:00:05 | INFO | EVENT | '
                        '{"event_type":"fire","camera_id":"b",'
                        '"description":"rtsp://admin:secret123@10.0.0.5/s"}\n'
                    )

            task = asyncio.create_task(appender())
            frame = None
            try:
                # 첫 프레임은 ": connected" heartbeat — alert 프레임이 나올 때까지 읽는다.
                for _ in range(6):
                    f = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    if f.startswith("event: alert"):
                        frame = f
                        break
            finally:
                await task
                await gen.aclose()

        self.assertIsNotNone(frame, "신규 이벤트의 alert 프레임이 push되지 않음")
        assert frame is not None
        self.assertIn('"event_type": "fire"', frame)  # 신규 이벤트만
        self.assertNotIn("old", frame)                # 초기 라인은 skip
        self.assertNotIn("secret123", frame)          # 마스킹 유지


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestModelUpload(unittest.TestCase):
    """업로드 엔드포인트 plumbing — 멀티파트 파싱·인증·크기 가드·service 위임.

    실제 ONNX 검증(샌드박스)은 service.handle_model_upload(→upload_validator)에 위임하며
    그 로직은 test_upload_validator.py가 검증한다. 여기서는 API 어댑터만 단위 검증한다.
    """

    def setUp(self):
        self.client = TestClient(create_app())

    def _post(self, content=b"onnxbytes", data=None):
        return self.client.post(
            "/api/models/upload",
            headers={"X-API-Key": _KEY},
            files={"file": ("m.onnx", content, "application/octet-stream")},
            data=data or {"model_name": "t", "classes": "a,b", "input_height": "640", "input_width": "640"},
        )

    def test_upload_success_delegates_and_returns_profile(self):
        fake = {"success": True, "model_id": "abc", "profile_name": "uploaded_abc", "enabled": False}
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}), \
                patch.object(service, "handle_model_upload", return_value=fake) as m:
            r = self._post()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["profile_name"], "uploaded_abc")
        self.assertFalse(r.json()["enabled"])  # 항상 disabled 등록
        # service에 정제된 인자가 전달됐는지(쉼표 분리 classes)
        _, kwargs = m.call_args
        self.assertEqual(kwargs["classes"], ["a", "b"])
        self.assertEqual(kwargs["input_size"], [640, 640])

    def test_upload_validation_failure_returns_400(self):
        fail = {"success": False, "error": "허용되지 않은 파일 형식입니다."}
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}), \
                patch.object(service, "handle_model_upload", return_value=fail):
            r = self._post()
        self.assertEqual(r.status_code, 400)
        self.assertIn("허용되지", r.json()["detail"])

    def test_upload_requires_auth(self):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = self.client.post(
                "/api/models/upload",
                files={"file": ("m.onnx", b"x", "application/octet-stream")},
                data={"model_name": "t"},
            )
        self.assertEqual(r.status_code, 401)  # 키 누락 → fail-closed

    def test_upload_rejects_oversized_before_validation(self):
        from src.models import upload_validator
        called = {"v": False}

        def _should_not_run(**_kw):
            called["v"] = True
            return {"success": True}

        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}), \
                patch.object(upload_validator, "MAX_FILE_SIZE_BYTES", 8), \
                patch.object(service, "handle_model_upload", side_effect=_should_not_run):
            r = self._post(content=b"way-too-large-payload")
        self.assertEqual(r.status_code, 413)
        self.assertFalse(called["v"], "크기 초과인데 검증기가 호출됨(OOM 가드 실패)")


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestCaptures(unittest.TestCase):
    """캡처 갤러리 — 트리 목록·경로 안전(traversal/확장자)·암호화 복호화."""

    def _make(self, tmp):
        d = Path(tmp) / "20260617" / "cam_001"
        d.mkdir(parents=True)
        (d / "person_detected_103045.jpg").write_bytes(b"\xff\xd8\xffPLAINJPG")
        return Path(tmp)

    def test_list_captures_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = service.list_captures(captures_dir=self._make(tmp))
        self.assertEqual(res["dates"][0]["date"], "20260617")
        cam = res["dates"][0]["cameras"][0]
        self.assertEqual(cam["camera_id"], "cam_001")
        self.assertEqual(cam["images"][0]["name"], "person_detected_103045.jpg")
        self.assertFalse(cam["images"][0]["encrypted"])

    def test_read_plain_returns_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._make(tmp)
            data = service.read_capture_image("20260617", "cam_001", "person_detected_103045.jpg", captures_dir=base)
        self.assertEqual(data, b"\xff\xd8\xffPLAINJPG")

    def test_read_rejects_unsafe(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._make(tmp)
            f = service.read_capture_image
            self.assertIsNone(f("20260617", "..", "person_detected_103045.jpg", captures_dir=base))  # dotdot
            self.assertIsNone(f("2026", "cam_001", "person_detected_103045.jpg", captures_dir=base))  # 잘못된 날짜
            self.assertIsNone(f("20260617", "cam_001", "evil.txt", captures_dir=base))  # 비-jpg 확장자
            self.assertIsNone(f("20260617", "cam_001", "nope.jpg", captures_dir=base))  # 부재

    def test_encrypted_requires_key(self):
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            self.skipTest("cryptography 미설치")
        key = Fernet.generate_key()
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp) / "20260617" / "cam_001"
            d.mkdir(parents=True)
            (d / "fire_120000.jpg.enc").write_bytes(Fernet(key).encrypt(b"\xff\xd8\xffSECRET"))
            base = Path(tmp)
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("KSI_CCTV_CAPTURE_KEY", None)
                self.assertIsNone(
                    service.read_capture_image("20260617", "cam_001", "fire_120000.jpg.enc", captures_dir=base)
                )
            with patch.dict(os.environ, {"KSI_CCTV_CAPTURE_KEY": key.decode()}):
                data = service.read_capture_image("20260617", "cam_001", "fire_120000.jpg.enc", captures_dir=base)
        self.assertEqual(data, b"\xff\xd8\xffSECRET")

    def test_endpoint_serves_and_auths(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = self._make(tmp)
            client = TestClient(create_app())
            with patch.object(service, "_CAPTURES_DIR", base), \
                    patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                # 목록
                r = client.get("/api/captures", headers={"X-API-Key": _KEY})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["dates"][0]["date"], "20260617")
                # 이미지(image/jpeg)
                r2 = client.get(
                    "/api/captures/20260617/cam_001/person_detected_103045.jpg",
                    headers={"X-API-Key": _KEY},
                )
                self.assertEqual(r2.status_code, 200)
                self.assertEqual(r2.headers["content-type"], "image/jpeg")
                self.assertEqual(r2.content, b"\xff\xd8\xffPLAINJPG")
                # 부재 → 404
                self.assertEqual(
                    client.get("/api/captures/20260617/cam_001/x.jpg", headers={"X-API-Key": _KEY}).status_code, 404
                )
                # 미인증 → 401
                self.assertEqual(client.get("/api/captures").status_code, 401)


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestModelDelete(unittest.TestCase):
    """업로드 모델 삭제 — uploaded만 허용(빌트인 보호)·디렉토리 정리·엔드포인트."""

    def _profiles(self, tmp):
        import yaml
        p = Path(tmp) / "model_profiles.yaml"
        mid = "abc123"
        updir = Path(tmp) / "uploaded" / mid
        updir.mkdir(parents=True)
        (updir / "model.onnx").write_bytes(b"x")
        cfg = {"models": {
            "uploaded_abc123": {"source": "uploaded", "type": "yolo_onnx",
                                "model_path": str(updir / "model.onnx"), "enabled": False},
            "general_yolo": {"source": "built_in", "type": "yolo_onnx", "enabled": True},
        }}
        p.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        return p, Path(tmp) / "uploaded", updir

    def test_delete_uploaded_removes_profile_and_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, base, updir = self._profiles(tmp)
            res = service.delete_uploaded_model("uploaded_abc123", profiles_path=p, upload_base_dir=base)
            self.assertTrue(res["deleted"])
            self.assertTrue(res["removed_dir"])
            self.assertFalse(updir.exists())
            import yaml
            left = yaml.safe_load(p.read_text())["models"]
            self.assertNotIn("uploaded_abc123", left)
            self.assertIn("general_yolo", left)  # 빌트인 보존

    def test_delete_builtin_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, base, _ = self._profiles(tmp)
            with self.assertRaises(ValueError):
                service.delete_uploaded_model("general_yolo", profiles_path=p, upload_base_dir=base)

    def test_delete_missing_raises_keyerror(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, base, _ = self._profiles(tmp)
            with self.assertRaises(KeyError):
                service.delete_uploaded_model("nope", profiles_path=p, upload_base_dir=base)

    def test_delete_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            p, base, _ = self._profiles(tmp)
            client = TestClient(create_app())
            with patch.object(service, "_MODEL_PROFILES", p), \
                    patch.object(service, "_ROOT", Path(tmp)), \
                    patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                # uploaded 삭제 → 200
                r = client.request("DELETE", "/api/models/uploaded_abc123", headers={"X-API-Key": _KEY})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertTrue(r.json()["deleted"])
                # 빌트인 삭제 → 400
                r2 = client.request("DELETE", "/api/models/general_yolo", headers={"X-API-Key": _KEY})
                self.assertEqual(r2.status_code, 400)
                # 미인증 → 401
                self.assertEqual(client.request("DELETE", "/api/models/x").status_code, 401)


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestSystemInfo(unittest.TestCase):
    """시스템 상태 — 리소스 메트릭 스냅샷 + 헬스 컴포넌트."""

    def test_metrics_shape(self):
        m = service.get_system_metrics()
        self.assertIsInstance(m, dict)
        # psutil 있으면 핵심 키 존재(없으면 {} — 둘 다 허용)
        if m:
            self.assertIn("cpu_percent", m)
            self.assertIn("memory_percent", m)
            self.assertIn("disk_percent", m)

    def test_endpoint_returns_metrics_and_health(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.get("/api/system", headers={"X-API-Key": _KEY})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIn("metrics", body)
            self.assertIn("health", body)
            self.assertIn("components", body["health"])
            # 미인증 → 401
            self.assertEqual(client.get("/api/system").status_code, 401)


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestAlertPreferences(unittest.TestCase):
    """알림 환경설정 — 읽기 기본값·저장 검증·임계값 검증·엔드포인트."""

    def test_get_defaults_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "alert_preferences.yaml"
            prefs = service.get_alert_preferences(prefs_path=p)
        self.assertEqual(prefs["email_alert_level_threshold"], "medium")
        self.assertEqual(prefs["per_event_email_enabled"], {})

    def test_set_and_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "alert_preferences.yaml"
            res = service.set_alert_preferences("high", {"fire": True, "person_detected": False}, prefs_path=p)
            self.assertEqual(res["enabled_count"], 1)
            got = service.get_alert_preferences(prefs_path=p)
        self.assertEqual(got["email_alert_level_threshold"], "high")
        self.assertEqual(got["per_event_email_enabled"], {"fire": True, "person_detected": False})

    def test_set_invalid_threshold_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "alert_preferences.yaml"
            with self.assertRaises(ValueError):
                service.set_alert_preferences("urgent", {}, prefs_path=p)

    def test_endpoint_get_put(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "alert_preferences.yaml"
            client = TestClient(create_app())
            with patch.object(service, "_ALERT_PREFS", p), patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                h = {"X-API-Key": _KEY}
                self.assertEqual(client.get("/api/alert-preferences", headers=h).json()["email_alert_level_threshold"], "medium")
                r = client.put("/api/alert-preferences", headers=h,
                               json={"email_alert_level_threshold": "critical", "per_event_email_enabled": {"fire": True}})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["email_alert_level_threshold"], "critical")
                # 영속 확인
                self.assertEqual(client.get("/api/alert-preferences", headers=h).json()["per_event_email_enabled"], {"fire": True})
                # 잘못된 임계값 → 400
                self.assertEqual(client.put("/api/alert-preferences", headers=h,
                                            json={"email_alert_level_threshold": "x"}).status_code, 400)
                # 미인증 → 401
                self.assertEqual(client.get("/api/alert-preferences").status_code, 401)


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestAudit(unittest.TestCase):
    """감사 로그 — write/list 라운드트립·마스킹·엔드포인트·쓰기엔드포인트 자동 기록."""

    def test_write_and_list_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "audit.jsonl"
            service.write_audit("e1", audit_log=p, model="a")
            service.write_audit("e2", audit_log=p, model="b")
            items = service.list_audit(audit_log=p)
        self.assertEqual([i["event"] for i in items], ["e2", "e1"])  # 최신순
        self.assertEqual(items[0]["source"], "api")
        self.assertEqual(items[0]["model"], "b")

    def test_list_masks_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "audit.jsonl"
            service.write_audit("model_upload_failed", audit_log=p, error="rtsp://admin:secret999@h/s")
            items = service.list_audit(audit_log=p)
        self.assertNotIn("secret999", items[0]["error"])

    def test_endpoint_lists_and_auths(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "audit.jsonl"
            service.write_audit("model_enabled_set", audit_log=p, model="x", enabled=True)
            client = TestClient(create_app())
            with patch.object(service, "_AUDIT_LOG", p), patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                r = client.get("/api/audit", headers={"X-API-Key": _KEY})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["entries"][0]["event"], "model_enabled_set")
                self.assertEqual(client.get("/api/audit").status_code, 401)  # 미인증

    def test_upload_endpoint_writes_audit(self):
        """엔진 쓰기 작업(업로드)이 감사 트레일에 기록되는지 — 웹콘솔 작업 미감사 갭 해소 확인."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "audit.jsonl"
            client = TestClient(create_app())
            fake = {"success": True, "profile_name": "uploaded_x", "model_id": "x", "enabled": False}
            with patch.object(service, "_AUDIT_LOG", p), \
                    patch.object(service, "handle_model_upload", return_value=fake), \
                    patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                r = client.post(
                    "/api/models/upload",
                    headers={"X-API-Key": _KEY},
                    files={"file": ("m.onnx", b"x", "application/octet-stream")},
                    data={"model_name": "감사 테스트"},
                )
                self.assertEqual(r.status_code, 200)
                entries = service.list_audit(audit_log=p)
        self.assertEqual(entries[0]["event"], "model_upload")
        self.assertEqual(entries[0]["profile_name"], "uploaded_x")


if __name__ == "__main__":
    unittest.main()
