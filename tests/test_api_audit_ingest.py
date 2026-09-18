"""콘솔 감사 기록 엔드포인트 `POST /api/audit/entry` 테스트.

내부용(콘솔 서버사이드·API 키 보유) 엔드포인트로, 콘솔 사용자 관리
(생성·수정·삭제·비밀번호 재설정)를 단일 감사 트레일에 기록한다.
actor는 요청 헤더 ContextVar(`current_actor()`)만 사용하고, body의
actor/actor 관련 키는 **무시**한다(스푸핑 방지 — 본 파일이 그 선택을 고정).
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.append(str(Path(__file__).parent.parent))

from src.api import service
from src.api.actor import set_actor

try:
    from fastapi.testclient import TestClient

    from src.api.app import create_app

    _HAS_API = True
except Exception:
    _HAS_API = False

_KEY = "test-api-key-strong"


def _read_audit_entries(path: Path) -> list:
    entries = []
    if not path.is_file():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestAuditEntryIngest(unittest.TestCase):
    def setUp(self):
        set_actor("system")
        self.client = TestClient(create_app())

    def tearDown(self):
        set_actor("system")

    def _post(self, body, headers=None, env=True):
        base = {"X-API-Key": _KEY}
        if headers:
            base.update(headers)
        if env:
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                return self.client.post("/api/audit/entry", headers=base, json=body)
        return self.client.post("/api/audit/entry", headers=base, json=body)

    def test_success_records_event_fields_and_header_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            with patch.object(service, "_AUDIT_LOG", audit):
                response = self._post(
                    {"event": "console_user_created", "fields": {"username": "op1", "role": "viewer"}},
                    headers={"X-KSI-Actor": "operator-1"},
                )
            entries = _read_audit_entries(audit)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True})
        matches = [e for e in entries if e.get("event") == "console_user_created"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["username"], "op1")
        self.assertEqual(matches[0]["role"], "viewer")
        self.assertEqual(matches[0]["actor"], "operator-1")

    def test_invalid_event_rejected_400(self):
        for bad in ("Bad Event!", "", "UPPER", "a-b", "a" * 65, "한글", "a/b"):
            with self.subTest(event=bad):
                response = self._post({"event": bad})
                self.assertEqual(response.status_code, 400, response.text)
        response = self._post({"fields": {"a": 1}})
        self.assertEqual(response.status_code, 400, response.text)

    def test_forbidden_key_top_level_400(self):
        for key in ("password", "Password", "PASSWORD_HASH", "salt", "Token", "SECRET"):
            with self.subTest(key=key):
                response = self._post({"event": "console_user_created", "fields": {key: "x"}})
                self.assertEqual(response.status_code, 400, response.text)

    def test_forbidden_key_nested_400(self):
        response = self._post(
            {"event": "console_user_updated", "fields": {"user": {"secret": "x"}}}
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_oversize_fields_400(self):
        response = self._post(
            {"event": "console_user_created", "fields": {"blob": "x" * 5000}}
        )
        self.assertEqual(response.status_code, 400, response.text)

    def test_non_object_fields_400(self):
        for bad in ([1, 2], "nope", 42):
            with self.subTest(fields=bad):
                response = self._post({"event": "console_user_created", "fields": bad})
                self.assertEqual(response.status_code, 400, response.text)

    def test_body_actor_is_ignored_header_actor_wins(self):
        """body 최상위 actor + fields 내 actor는 무시 — 헤더 actor만 기록."""
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            with patch.object(service, "_AUDIT_LOG", audit):
                response = self._post(
                    {
                        "event": "console_user_deleted",
                        "fields": {"actor": "evil", "username": "op1"},
                        "actor": "evil-top",
                    },
                    headers={"X-KSI-Actor": "operator-1"},
                )
            entries = _read_audit_entries(audit)
        self.assertEqual(response.status_code, 200, response.text)
        matches = [e for e in entries if e.get("event") == "console_user_deleted"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["actor"], "operator-1")
        self.assertNotIn("evil", json.dumps(matches[0], ensure_ascii=False))

    def test_fail_closed_without_key(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KSI_CCTV_API_KEY", None)
            response = self.client.post(
                "/api/audit/entry",
                headers={"X-API-Key": _KEY},
                json={"event": "console_user_created"},
            )
        self.assertEqual(response.status_code, 503)

    def test_wrong_key_rejected_401(self):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            response = self.client.post(
                "/api/audit/entry",
                headers={"X-API-Key": "wrong"},
                json={"event": "console_user_created"},
            )
        self.assertEqual(response.status_code, 401)

    def test_write_audit_failure_returns_503(self):
        with patch.object(service, "write_audit", return_value=False):
            response = self._post({"event": "console_user_created"})
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
