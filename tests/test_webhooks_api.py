"""WebhookEndpoint 관리 API 테스트 — CRUD·검증·인증·감사·마스킹·매트릭스 (P2-T3).

TDD RED: `src/api/routes/webhooks.py` 구현 전에는 임포트 단계에서 실패한다.
"""
import hashlib
import hmac
import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml

sys.path.append(str(Path(__file__).parent.parent))

from fastapi.testclient import TestClient

from src.api import service
from src.api.app import create_app
from src.api.routes import webhooks as wh

_KEY = "test-api-key-strong"

_audit_redirect = None


def setUpModule():
    global _audit_redirect
    _audit_redirect = tempfile.TemporaryDirectory()
    service._AUDIT_LOG = Path(_audit_redirect.name) / "audit.jsonl"


def tearDownModule():
    if _audit_redirect is not None:
        _audit_redirect.cleanup()


class _HookHandler(BaseHTTPRequestHandler):
    """loopback mock 수신기 — 요청 헤더·바디를 서버 객체에 기록한다."""

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        self.server.received.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        data = b'{"ok": true}'
        self.send_response(getattr(self.server, "reply_status", 200))
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class WebhookApiBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.webhooks_file = Path(self._tmp.name) / "webhooks.yaml"
        self.prefs_file = Path(self._tmp.name) / "alert_preferences.yaml"
        # I-5: 운영과 동일하게 env 경유로 경로를 주입한다.
        # 모듈 상수 직접 패치는 env 분기를 우회하므로 사용 금지.
        self._env = patch.dict(
            os.environ,
            {
                "KSI_CCTV_API_KEY": _KEY,
                wh._WEBHOOKS_ENV: str(self.webhooks_file),
                wh._PREFS_ENV: str(self.prefs_file),
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        self.client = TestClient(create_app())
        self.h = {"X-API-Key": _KEY}

    def _create(self, name="hook-1", url="https://example.com/hook"):
        r = self.client.post(
            "/api/webhooks", headers=self.h, json={"name": name, "url": url}
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["endpoint"]

    def _audit_events(self):
        return service.list_audit()

    def _find_audit(self, event):
        for entry in self._audit_events():
            if entry.get("event") == event:
                return entry
        return None


class TestWebhookCrud(WebhookApiBase):
    def test_create_returns_secret_once_and_list_masks(self):
        ep = self._create()
        secret = ep["secret"]
        self.assertEqual(len(ep["id"]), 16)
        self.assertEqual(len(secret), 64)  # token_hex(32) 1회 평문
        self.assertEqual(ep["name"], "hook-1")

        body = self.client.get("/api/webhooks", headers=self.h).json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["endpoints"][0]["secret"], "***")
        self.assertNotIn(secret, json.dumps(body))

        entry = self._find_audit("webhook_created")
        self.assertIsNotNone(entry)
        self.assertNotIn(secret, json.dumps(entry))  # 감사에 secret 절대 미기록
        self.assertEqual(entry["url"], "https://example.com/hook")

    def test_create_rejects_bad_urls(self):
        bad = [
            "http://user:pass@example.com/hook",  # 자격정보 포함
            "ftp://example.com/hook",  # 非http(s)
            "not-a-url",  # scheme·호스트 없음
        ]
        for url in bad:
            r = self.client.post(
                "/api/webhooks", headers=self.h, json={"name": "bad", "url": url}
            )
            self.assertEqual(r.status_code, 422, url)

    def test_create_rejects_empty_name(self):
        r = self.client.post(
            "/api/webhooks", headers=self.h, json={"name": "  ", "url": "https://example.com/hook"}
        )
        self.assertEqual(r.status_code, 422)

    def test_update_and_delete_roundtrip(self):
        ep = self._create()
        eid = ep["id"]
        r = self.client.put(
            f"/api/webhooks/{eid}",
            headers=self.h,
            json={"name": "renamed", "enabled": False},
        )
        self.assertEqual(r.status_code, 200, r.text)
        updated = r.json()["endpoint"]
        self.assertEqual(updated["name"], "renamed")
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["secret"], "***")  # 수정 응답은 마스킹

        r = self.client.put(
            f"/api/webhooks/{eid}", headers=self.h, json={"url": "ftp://x/y"}
        )
        self.assertEqual(r.status_code, 422)

        self.assertIsNotNone(self._find_audit("webhook_updated"))

        r = self.client.delete(f"/api/webhooks/{eid}", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.client.get("/api/webhooks", headers=self.h).json()["count"], 0)
        entry = self._find_audit("webhook_deleted")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["endpoint_id"], eid)

    def test_unknown_id_returns_404(self):
        self.assertEqual(
            self.client.put("/api/webhooks/deadbeefdeadbeef", headers=self.h, json={"name": "x"}).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete("/api/webhooks/deadbeefdeadbeef", headers=self.h).status_code, 404
        )
        self.assertEqual(
            self.client.post("/api/webhooks/deadbeefdeadbeef/rotate", headers=self.h).status_code,
            404,
        )
        self.assertEqual(
            self.client.post("/api/webhooks/deadbeefdeadbeef/test", headers=self.h).status_code,
            404,
        )

    def test_rotate_returns_new_secret_once(self):
        ep = self._create()
        old_secret = ep["secret"]
        r = self.client.post(f"/api/webhooks/{ep['id']}/rotate", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        rotated = r.json()["endpoint"]
        self.assertNotEqual(rotated["secret"], old_secret)  # 구 secret 무효
        self.assertEqual(len(rotated["secret"]), 64)
        self.assertIsNotNone(rotated["rotated_at"])

        body = self.client.get("/api/webhooks", headers=self.h).json()
        self.assertEqual(body["endpoints"][0]["secret"], "***")
        self.assertNotIn(rotated["secret"], json.dumps(body))

        entry = self._find_audit("webhook_rotated")
        self.assertIsNotNone(entry)
        self.assertNotIn(rotated["secret"], json.dumps(entry))

    def test_unauthorized_is_401(self):
        self.assertEqual(self.client.get("/api/webhooks").status_code, 401)
        self.assertEqual(
            self.client.post(
                "/api/webhooks", json={"name": "x", "url": "https://example.com/hook"}
            ).status_code,
            401,
        )

    def test_store_file_is_0600(self):
        self.assertFalse(self.webhooks_file.exists())
        self._create()  # 최초 생성 경로에서 바로 0o600 (transient window 없음)
        mode = stat.S_IMODE(os.stat(self.webhooks_file).st_mode)
        self.assertEqual(mode, 0o600)

    def test_interrupted_write_preserves_old_file(self):
        """I-2: 쓰기 중단(kill -9 시뮬레이션)은 구 파일을 보존하고 temp를 남기지 않는다."""
        ep = self._create()
        before = self.webhooks_file.read_bytes()
        client = TestClient(create_app(), raise_server_exceptions=False)
        with patch.object(wh._yaml_rt, "dump", side_effect=RuntimeError("kill -9")):
            r = client.post(f"/api/webhooks/{ep['id']}/rotate", headers=self.h)
        self.assertEqual(r.status_code, 500)
        self.assertEqual(self.webhooks_file.read_bytes(), before)
        leftovers = [
            p.name
            for p in self.webhooks_file.parent.iterdir()
            if p.name != self.webhooks_file.name
        ]
        self.assertEqual(leftovers, [])

    def test_empty_update_is_422_without_side_effects(self):
        """M-6: 빈 PUT은 파일 재기록·감사 없이 422."""
        ep = self._create()
        before = self.webhooks_file.read_bytes()
        r = self.client.put(f"/api/webhooks/{ep['id']}", headers=self.h, json={})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.webhooks_file.read_bytes(), before)
        self.assertIsNone(self._find_audit("webhook_updated"))

    def test_paths_come_from_env(self):
        """I-5: env 지정 경로를 그대로 사용한다(기본 tmp 경로에는 손대지 않음)."""
        alt_w = Path(self._tmp.name) / "alt-webhooks.yaml"
        alt_p = Path(self._tmp.name) / "alt-prefs.yaml"
        with patch.dict(
            os.environ, {wh._WEBHOOKS_ENV: str(alt_w), wh._PREFS_ENV: str(alt_p)}
        ):
            self._create(name="via-env")
            r = self.client.put(
                "/api/webhooks/channels",
                headers=self.h,
                json={"channels": {"fire": ["webhook"]}},
            )
            self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(alt_w.exists())
        self.assertFalse(self.webhooks_file.exists())
        self.assertTrue(alt_p.exists())
        self.assertFalse(self.prefs_file.exists())

    def test_save_preserves_comments(self):
        self.webhooks_file.write_text(
            "# 웹훅 엔드포인트 — 시크릿 포함, 0o600 유지\n"
            "version: 1\n"
            "endpoints: []\n",
            encoding="utf-8",
        )
        self._create(name="c1")
        raw = self.webhooks_file.read_text(encoding="utf-8")
        self.assertIn("# 웹훅 엔드포인트 — 시크릿 포함, 0o600 유지", raw)

    def test_reader_is_compatible_with_t2_provider(self):
        """T2 `webhook_endpoint_provider`에 그대로 주입 가능한 리더."""
        from src.events.models import WebhookEndpoint

        ep = self._create(url="https://example.com/t2")
        loaded = wh.load_webhook_endpoints()
        self.assertEqual(len(loaded), 1)
        self.assertIsInstance(loaded[0], WebhookEndpoint)
        provider = lambda: wh.load_webhook_endpoints()  # noqa: E731
        self.assertEqual(provider()[0].id, ep["id"])
        self.assertTrue(provider()[0].enabled)


class TestWebhookTestPing(WebhookApiBase):
    def _serve(self, status=200):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _HookHandler)
        server.received = []
        server.reply_status = status
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server

    def _no_proxy_env(self):
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        env["KSI_CCTV_API_KEY"] = _KEY
        return patch.dict(os.environ, env, clear=True)

    def test_ping_delivers_signed_request(self):
        server = self._serve(200)
        url = f"http://127.0.0.1:{server.server_port}/hook"
        with self._no_proxy_env():
            ep = self._create(url=url)
            r = self.client.post(f"/api/webhooks/{ep['id']}/test", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["ok"])

        self.assertEqual(len(server.received), 1)
        req = server.received[0]
        payload = json.loads(req["body"].decode("utf-8"))
        self.assertEqual(payload["version"], "v1")
        self.assertEqual(payload["type"], "test")
        expected = hmac.new(ep["secret"].encode("utf-8"), req["body"], hashlib.sha256).hexdigest()
        lowered = {k.lower(): v for k, v in req["headers"].items()}
        self.assertEqual(lowered.get("x-ksi-signature"), expected)
        self.assertIn("x-ksi-delivery", lowered)

        entry = self._find_audit("webhook_tested")
        self.assertIsNotNone(entry)
        self.assertNotIn(ep["secret"], json.dumps(entry))

    def test_ping_failure_returns_not_ok(self):
        server = self._serve(500)
        url = f"http://127.0.0.1:{server.server_port}/hook"
        with self._no_proxy_env():
            ep = self._create(url=url)
            r = self.client.post(f"/api/webhooks/{ep['id']}/test", headers=self.h)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(r.json()["ok"])

    def test_ping_disabled_endpoint_is_409(self):
        ep = self._create()
        self.client.put(f"/api/webhooks/{ep['id']}", headers=self.h, json={"enabled": False})
        r = self.client.post(f"/api/webhooks/{ep['id']}/test", headers=self.h)
        self.assertEqual(r.status_code, 409)

    def test_rotate_invalidates_old_secret(self):
        """I-6: rotate 실효 — 신 secret 서명 성공·구 secret 거부·파일 영속."""
        server = self._serve(200)
        url = f"http://127.0.0.1:{server.server_port}/hook"
        with self._no_proxy_env():
            ep = self._create(url=url)
            old_secret = ep["secret"]
            self.assertTrue(
                self.client.post(f"/api/webhooks/{ep['id']}/test", headers=self.h).json()["ok"]
            )
            rotated = self.client.post(
                f"/api/webhooks/{ep['id']}/rotate", headers=self.h
            ).json()["endpoint"]
            new_secret = rotated["secret"]
            self.assertTrue(
                self.client.post(f"/api/webhooks/{ep['id']}/test", headers=self.h).json()["ok"]
            )
        stored = yaml.safe_load(self.webhooks_file.read_text(encoding="utf-8"))
        self.assertEqual(stored["endpoints"][0]["secret"], new_secret)
        self.assertEqual(len(server.received), 2)
        last = server.received[-1]
        expected = hmac.new(new_secret.encode("utf-8"), last["body"], hashlib.sha256).hexdigest()
        stale = hmac.new(old_secret.encode("utf-8"), last["body"], hashlib.sha256).hexdigest()
        lowered = {k.lower(): v for k, v in last["headers"].items()}
        self.assertEqual(lowered.get("x-ksi-signature"), expected)
        self.assertNotEqual(lowered.get("x-ksi-signature"), stale)


class TestWebhookChannels(WebhookApiBase):
    """T2 리뷰 M-1 이관 — 채널 매핑 쓰기 값은 list[string] 강제(문자열 오타 조용한 차단 방지)."""

    def test_put_valid_matrix_roundtrip(self):
        self.prefs_file.write_text(
            "# 알림 환경설정 — SMTP는 환경변수\n"
            "email_alert_level_threshold: medium\n"
            "per_event_email_enabled: {}\n",
            encoding="utf-8",
        )
        matrix = {"intrusion": ["email", "webhook"], "fire": ["email"]}
        r = self.client.put("/api/webhooks/channels", headers=self.h, json={"channels": matrix})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["channels"], matrix)

        got = self.client.get("/api/webhooks/channels", headers=self.h).json()
        self.assertEqual(got["channels"], matrix)

        raw = self.prefs_file.read_text(encoding="utf-8")
        self.assertIn("# 알림 환경설정 — SMTP는 환경변수", raw)  # 주석 보존
        self.assertIn("email_alert_level_threshold: medium", raw)  # 타 키 보존

        entry = self._find_audit("webhook_channels_saved")
        self.assertIsNotNone(entry)

    def test_put_rejects_string_value(self):
        r = self.client.put(
            "/api/webhooks/channels", headers=self.h, json={"channels": {"fire": "email"}}
        )
        self.assertEqual(r.status_code, 422)

    def test_put_rejects_non_string_items(self):
        r = self.client.put(
            "/api/webhooks/channels", headers=self.h, json={"channels": {"fire": ["email", 123]}}
        )
        self.assertEqual(r.status_code, 422)

    def test_put_rejects_unknown_channel(self):
        r = self.client.put(
            "/api/webhooks/channels", headers=self.h, json={"channels": {"fire": ["sms"]}}
        )
        self.assertEqual(r.status_code, 422)

    def test_put_rejects_non_object(self):
        r = self.client.put(
            "/api/webhooks/channels", headers=self.h, json={"channels": ["email"]}
        )
        self.assertEqual(r.status_code, 422)

    def test_empty_matrix_without_confirm_is_422(self):
        """I-4: 확인 없는 전체 삭제({})는 422이며 파일을 건드리지 않는다."""
        self.prefs_file.write_text("email_alert_level_threshold: medium\n", encoding="utf-8")
        before = self.prefs_file.read_bytes()
        r = self.client.put("/api/webhooks/channels", headers=self.h, json={"channels": {}})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.prefs_file.read_bytes(), before)

    def test_empty_matrix_with_confirm_is_200(self):
        r = self.client.put(
            "/api/webhooks/channels",
            headers=self.h,
            json={"channels": {}, "confirm": True},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["channels"], {})

    def test_matrix_key_too_long_is_422(self):
        """M-2: 128자 초과 키는 silent 절단 대신 422."""
        r = self.client.put(
            "/api/webhooks/channels",
            headers=self.h,
            json={"channels": {"e" * 129: ["email"]}},
        )
        self.assertEqual(r.status_code, 422)
        r = self.client.put(
            "/api/webhooks/channels",
            headers=self.h,
            json={"channels": {"e" * 128: ["email"]}},
        )
        self.assertEqual(r.status_code, 200, r.text)

    def test_channels_route_not_shadowed_by_id_route(self):
        """M-5: 정적 /channels가 {endpoint_id}에 삼켜지면 404가 된다."""
        r = self.client.put(
            "/api/webhooks/channels", headers=self.h, json={"channels": {"fire": ["email"]}}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("channels", r.json())

    def test_channels_require_auth(self):
        self.assertEqual(self.client.get("/api/webhooks/channels").status_code, 401)


class TestWebhookOpenApi(unittest.TestCase):
    def test_new_paths_present_and_existing_tags_unchanged(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            spec = client.get("/openapi.json", headers={"X-API-Key": _KEY}).json()
        paths = spec["paths"]
        for path in (
            "/api/webhooks",
            "/api/webhooks/{endpoint_id}",
            "/api/webhooks/{endpoint_id}/rotate",
            "/api/webhooks/{endpoint_id}/test",
            "/api/webhooks/channels",
        ):
            self.assertIn(path, paths)
        self.assertEqual(paths["/api/alert-preferences"]["get"]["tags"], ["settings"])
        self.assertEqual(paths["/api/models"]["get"]["tags"], ["models"])


if __name__ == "__main__":
    unittest.main()
