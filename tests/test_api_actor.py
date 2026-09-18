"""R5a: `X-KSI-Actor` 헤더 → ContextVar → `write_audit` actor 기록.

콘솔(engine.ts)은 서버사이드에서 `X-KSI-Actor`를 전송하고(R5b), 엔진은
미들웨어에서 이를 요청 컨텍스트(ContextVar)로 캡처한다. `write_audit`는
기존 32개 호출부를 수정하지 않고 actor 필드를 기록한다(명시 인자 우선).
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
from src.api.actor import current_actor, sanitize_actor, set_actor

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


class TestSanitizeActor(unittest.TestCase):
    def test_keeps_normal_usernames(self):
        for name in ("admin", "operator-1", "user.name_2", "A" * 64):
            self.assertEqual(sanitize_actor(name), name)

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(sanitize_actor("  admin  "), "admin")

    def test_empty_or_blank_falls_back_to_system(self):
        self.assertEqual(sanitize_actor(""), "system")
        self.assertEqual(sanitize_actor("   "), "system")

    def test_too_long_falls_back_to_system(self):
        self.assertEqual(sanitize_actor("A" * 65), "system")

    def test_disallowed_characters_fall_back_to_system(self):
        for bad in ("a b", "a/b", "a;b", "a@b", "a\nb", "관제원", "a$b"):
            self.assertEqual(sanitize_actor(bad), "system", bad)

    def test_non_string_falls_back_to_system(self):
        self.assertEqual(sanitize_actor(None), "system")
        self.assertEqual(sanitize_actor(123), "system")


class TestCurrentActor(unittest.TestCase):
    def setUp(self):
        set_actor("system")

    def tearDown(self):
        set_actor("system")

    def test_default_is_system(self):
        self.assertEqual(current_actor(), "system")

    def test_set_and_current_roundtrip(self):
        set_actor("operator-1")
        self.assertEqual(current_actor(), "operator-1")

    def test_set_sanitizes_invalid_value(self):
        set_actor("a b")
        self.assertEqual(current_actor(), "system")


class TestWriteAuditActor(unittest.TestCase):
    def setUp(self):
        set_actor("system")

    def tearDown(self):
        set_actor("system")

    def test_write_audit_records_context_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            set_actor("operator-1")
            self.assertTrue(service.write_audit("actor_ctx", audit_log=path))
            entries = _read_audit_entries(path)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["actor"], "operator-1")

    def test_write_audit_explicit_actor_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            set_actor("operator-1")
            self.assertTrue(service.write_audit("actor_exp", audit_log=path, actor="explicit"))
            entries = _read_audit_entries(path)
        self.assertEqual(entries[0]["actor"], "explicit")

    def test_write_audit_default_actor_is_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            self.assertTrue(service.write_audit("actor_def", audit_log=path))
            entries = _read_audit_entries(path)
        self.assertEqual(entries[0]["actor"], "system")


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestActorMiddleware(unittest.TestCase):
    """미들웨어 통합: 헤더 → ContextVar → sync 핸드러(threadpool) → 감사 기록.

    incident_action 핸들러는 sync(`def`)라 threadpool에서 실행된다. 여기서
    actor가 일치하면 anyio 컨텍스트 복사로 ContextVar가 전파됨이 실측된다.
    """

    def setUp(self):
        set_actor("system")

    def tearDown(self):
        set_actor("system")

    def _make_client(self, tmp: str):
        from src.incidents import IncidentStore
        from src.storage.sqlite_events import SQLiteEventRepository

        store = IncidentStore(Path(tmp) / "incidents.db")
        events = SQLiteEventRepository(Path(tmp) / "events.db")
        events.record({"event_id": "event-1", "event_type": "fire"})
        client = TestClient(create_app(incident_store=store, event_repository=events))
        return client, store, events

    def test_header_actor_reaches_incident_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            client, store, events = self._make_client(tmp)
            try:
                with patch.object(service, "_AUDIT_LOG", audit), patch.dict(
                    os.environ, {"KSI_CCTV_API_KEY": _KEY}
                ):
                    response = client.post(
                        "/api/incidents/event-1/actions",
                        headers={"X-API-Key": _KEY, "X-KSI-Actor": "operator-1"},
                        json={"status": "acknowledged"},
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    incidents = _read_audit_entries(audit)
            finally:
                store.close()
                events.close()
        actions = [e for e in incidents if e.get("event") == "incident_action"]
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["actor"], "operator-1")

    def test_missing_or_invalid_header_falls_back_to_system(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            client, store, events = self._make_client(tmp)
            try:
                with patch.object(service, "_AUDIT_LOG", audit), patch.dict(
                    os.environ, {"KSI_CCTV_API_KEY": _KEY}
                ):
                    missing = client.post(
                        "/api/incidents/event-1/actions",
                        headers={"X-API-Key": _KEY},
                        json={"status": "acknowledged"},
                    )
                    self.assertEqual(missing.status_code, 200, missing.text)
                    invalid = client.post(
                        "/api/incidents/event-1/actions",
                        headers={"X-API-Key": _KEY, "X-KSI-Actor": "not valid!"},
                        json={"status": "in_progress"},
                    )
                    self.assertEqual(invalid.status_code, 200, invalid.text)
                    incidents = _read_audit_entries(audit)
            finally:
                store.close()
                events.close()
        actions = [e for e in incidents if e.get("event") == "incident_action"]
        self.assertEqual(len(actions), 2)
        self.assertTrue(all(e["actor"] == "system" for e in actions), actions)


if __name__ == "__main__":
    unittest.main()
