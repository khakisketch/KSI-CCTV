"""엔진 API(FastAPI) 테스트 — 인증 fail-closed, 시크릿 마스킹, 엔드포인트 스키마."""
import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.append(str(Path(__file__).parent.parent))

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

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

    def test_incident_actions_are_authenticated_durable_and_audited_without_notes(self):
        from src.incidents import IncidentStore
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            store = IncidentStore(Path(tmp) / "events.db")
            events = SQLiteEventRepository(Path(tmp) / "events.sqlite")
            events.record({"event_id": "event-1", "event_type": "fire"})
            events.record({"event_id": "camera-health-1", "event_type": "camera_disconnect", "alert_level": "high"})
            client = TestClient(create_app(incident_store=store, event_repository=events))
            secret_note = "현장 연락처 010-1234-5678"
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                self.assertEqual(client.get("/api/incidents").status_code, 401)
                created = client.post(
                    "/api/incidents/event-1/actions",
                    headers={"X-API-Key": _KEY},
                    json={"status": "acknowledged", "note": secret_note},
                )
                listed = client.get("/api/incidents", headers={"X-API-Key": _KEY})
                store.ensure(
                    "camera-health-1",
                    category="camera_health",
                    camera_id="cam-01",
                    source_event_type="camera_disconnect",
                )
                health_listed = client.get(
                    "/api/incidents?category=camera_health",
                    headers={"X-API-Key": _KEY},
                )
                detail = client.get("/api/incidents/event-1", headers={"X-API-Key": _KEY})
                invalid = client.post(
                    "/api/incidents/event-1/actions",
                    headers={"X-API-Key": _KEY},
                    json={"status": "resolved"},
                )

            self.assertEqual(created.status_code, 200, created.text)
            self.assertEqual(created.json()["status"], "acknowledged")
            self.assertEqual(listed.json()["incidents"][0]["event_id"], "event-1")
            self.assertEqual(health_listed.json()["incidents"][0]["category"], "camera_health")
            self.assertEqual(health_listed.json()["incidents"][0]["source_event_alert_level"], "high")
            self.assertEqual(detail.json()["actions"][0]["note"], secret_note)
            self.assertEqual(store.actions("event-1")[0]["note"], secret_note)
            self.assertEqual(invalid.status_code, 422)
            self.assertNotIn(secret_note, service._AUDIT_LOG.read_text(encoding="utf-8"))
            store.close()
            events.close()

    def test_incident_action_rejects_unknown_event_without_creating_record(self):
        from src.incidents import IncidentStore
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            store = IncidentStore(Path(tmp) / "incidents.db")
            events = SQLiteEventRepository(Path(tmp) / "events.db")
            client = TestClient(create_app(incident_store=store, event_repository=events))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                response = client.post(
                    "/api/incidents/missing-event/actions",
                    headers={"X-API-Key": _KEY},
                    json={"status": "acknowledged"},
                )

            self.assertEqual(response.status_code, 404)
            self.assertIsNone(store.get("missing-event"))
            self.assertEqual(store.actions("missing-event"), [])
            store.close()
            events.close()

    def test_models_endpoint_reports_registry_runtime_state(self):
        class Registry:
            @staticmethod
            def to_status_dict():
                return {
                    "general_yolo": {
                        "loaded": True,
                        "error": "",
                        "inference_count": 12,
                        "error_count": 1,
                        "last_latency_ms": 18.5,
                    },
                }

        class Pipeline:
            model_registry = Registry()

        client = TestClient(create_app(pipeline=Pipeline()))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            response = client.get("/api/models", headers=self.h)

        self.assertEqual(response.status_code, 200)
        general = next(item for item in response.json()["models"] if item["name"] == "general_yolo")
        self.assertEqual(general["runtime"], {
            "available": True,
            "loaded": True,
            "error": "",
            "inference_count": 12,
            "error_count": 1,
            "last_latency_ms": 18.5,
        })

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

    def test_events_filter_by_alert_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            lines = [
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_type":"fire","camera_id":"a","alert_level":"critical"}',
                '2026-06-16 10:00:01 | INFO | EVENT | {"event_type":"person_detected","camera_id":"a","alert_level":"low"}',
                '2026-06-16 10:00:02 | INFO | EVENT | {"event_type":"smoke","camera_id":"b","alert_level":"high"}',
                '2026-06-16 10:00:03 | INFO | EVENT | {"event_type":"fire","camera_id":"b","alert_level":"critical"}',
            ]
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            criticals = service.list_events(limit=10, alert_level="critical", alerts_log=log)
            self.assertEqual([e["camera_id"] for e in criticals], ["b", "a"])

    def test_events_filter_by_time_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            lines = [
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_type":"fire","camera_id":"a","timestamp":"2026-06-16T09:00:00Z"}',
                '2026-06-16 10:00:01 | INFO | EVENT | {"event_type":"fire","camera_id":"b","timestamp":"2026-06-16T10:30:00Z"}',
                '2026-06-16 10:00:02 | INFO | EVENT | {"event_type":"fire","camera_id":"c","timestamp":"2026-06-16T11:30:00Z"}',
                '2026-06-16 10:00:03 | INFO | EVENT | {"event_type":"fire","camera_id":"d","timestamp":"2026-06-16T12:30:00Z"}',
            ]
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            items = service.list_events(
                limit=10,
                since="2026-06-16T10:00:00Z",
                until="2026-06-16T12:00:00Z",
                alerts_log=log,
            )
            # b(10:30 UTC), c(11:30 UTC) 포함, d(12:30) 제외
            self.assertEqual([e["camera_id"] for e in items], ["c", "b"])

    def test_events_offset_pagination(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            lines = [
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_type":"fire","camera_id":"a"}',
                '2026-06-16 10:00:01 | INFO | EVENT | {"event_type":"fire","camera_id":"b"}',
                '2026-06-16 10:00:02 | INFO | EVENT | {"event_type":"fire","camera_id":"c"}',
                '2026-06-16 10:00:03 | INFO | EVENT | {"event_type":"fire","camera_id":"d"}',
            ]
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            page1 = service.list_events(limit=2, offset=0, alerts_log=log)
            page2 = service.list_events(limit=2, offset=2, alerts_log=log)
            self.assertEqual([e["camera_id"] for e in page1], ["d", "c"])
            self.assertEqual([e["camera_id"] for e in page2], ["b", "a"])

    def test_events_combined_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            lines = [
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_type":"fire","camera_id":"a","alert_level":"critical","timestamp":"2026-06-16T09:00:00Z"}',
                '2026-06-16 10:00:01 | INFO | EVENT | {"event_type":"fire","camera_id":"a","alert_level":"high","timestamp":"2026-06-16T10:30:00Z"}',
                '2026-06-16 10:00:02 | INFO | EVENT | {"event_type":"fire","camera_id":"a","alert_level":"critical","timestamp":"2026-06-16T11:30:00Z"}',
                '2026-06-16 10:00:03 | INFO | EVENT | {"event_type":"smoke","camera_id":"a","alert_level":"critical","timestamp":"2026-06-16T11:30:00Z"}',
            ]
            log.write_text("\n".join(lines) + "\n", encoding="utf-8")
            items = service.list_events(
                limit=10,
                event_type="fire",
                alert_level="critical",
                since="2026-06-16T10:00:00Z",
                until="2026-06-16T12:00:00Z",
                alerts_log=log,
            )
            self.assertEqual([e.get("camera_id") for e in items], ["a"])

    def test_events_collapse_lifecycle_updates_by_event_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                "\n".join([
                    '2026-06-16 10:00:00 | INFO | EVENT | {"event_id":"evt-1","event_type":"fire","alert_state":"provisional"}',
                    '2026-06-16 10:00:01 | INFO | EVENT | {"event_id":"evt-1","event_type":"fire","alert_state":"confirmed"}',
                ]) + "\n",
                encoding="utf-8",
            )

            items = service.list_events(limit=10, alerts_log=log)

            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["alert_state"], "confirmed")

    def test_events_endpoint_uses_durable_repository_when_supplied(self):
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteEventRepository(Path(tmp) / "events.db")
            repository.record({
                "event_id": "durable-1",
                "event_type": "fire",
                "camera_id": "cam-durable",
                "timestamp": "2026-07-26T00:00:00Z",
                "alert_level": "critical",
                "description": "persisted event",
            })
            client = TestClient(create_app(event_repository=repository))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                response = client.get("/api/events?limit=10", headers={"X-API-Key": _KEY})
            repository.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["events"][0]["event_id"], "durable-1")

    def test_durable_event_store_degradation_is_reported_and_recovers(self):
        class FlakyRepository:
            failing = True

            @classmethod
            def list(cls, **_kwargs):
                if cls.failing:
                    raise RuntimeError("event payload secret-value")
                return [{"event_id": "durable-1", "event_type": "fire"}]

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_id":"legacy-1"}\n',
                encoding="utf-8",
            )
            client = TestClient(create_app(event_repository=FlakyRepository()))
            with patch.object(service, "_ALERTS_LOG", log), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ), self.assertLogs("src.api.service", level="WARNING") as logs:
                fallback = client.get("/api/events", headers={"X-API-Key": _KEY})
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                degraded = client.get("/api/system", headers={"X-API-Key": _KEY})
                FlakyRepository.failing = False
                durable = client.get("/api/events", headers={"X-API-Key": _KEY})
                recovered = client.get("/api/system", headers={"X-API-Key": _KEY})

        self.assertEqual(fallback.status_code, 200)
        self.assertEqual(fallback.json()["events"][0]["event_id"], "legacy-1")
        self.assertNotIn("secret-value", "\n".join(logs.output))
        degraded_component = next(
            component for component in degraded.json()["health"]["components"]
            if component["name"] == "durable_event_store"
        )
        self.assertFalse(degraded_component["healthy"])
        self.assertEqual(degraded_component["metadata"]["last_error_type"], "RuntimeError")
        self.assertEqual(durable.json()["events"][0]["event_id"], "durable-1")
        recovered_component = next(
            component for component in recovered.json()["health"]["components"]
            if component["name"] == "durable_event_store"
        )
        self.assertTrue(recovered_component["healthy"])
        self.assertIsNone(recovered_component["metadata"]["last_error_type"])

    def test_durable_event_store_tracks_query_and_write_failures_independently(self):
        status = service.DurableEventStoreStatus()
        status.record_failure(OSError("disk full"), operation="write")
        status.record_success(operation="query")

        degraded = status.health_check()
        self.assertFalse(degraded.healthy)
        self.assertEqual(degraded.metadata["failed_operations"], ["write"])
        self.assertEqual(degraded.metadata["last_error_type"], "OSError")

        status.record_success(operation="write")
        recovered = status.health_check()
        self.assertTrue(recovered.healthy)
        self.assertEqual(recovered.metadata["failed_operations"], [])

    def test_durable_event_detail_failure_degrades_health_and_matching_success_recovers(self):
        class FlakyDetailRepository:
            failing = True

            @classmethod
            def get(cls, _event_id):
                if cls.failing:
                    raise OSError("disk failure with sensitive payload")
                return {"event_id": "detail-1", "event_type": "fire"}

        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_id":"detail-1","event_type":"fire"}\n',
                encoding="utf-8",
            )
            client = TestClient(create_app(event_repository=FlakyDetailRepository()))
            with patch.object(service, "_ALERTS_LOG", log), patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                fallback = client.get("/api/events/detail-1", headers={"X-API-Key": _KEY})
                degraded = client.get("/api/system", headers={"X-API-Key": _KEY})
                FlakyDetailRepository.failing = False
                durable = client.get("/api/events/detail-1", headers={"X-API-Key": _KEY})
                recovered = client.get("/api/system", headers={"X-API-Key": _KEY})

        self.assertEqual(fallback.status_code, 200)
        detail_component = next(
            component for component in degraded.json()["health"]["components"]
            if component["name"] == "durable_event_store"
        )
        self.assertEqual(detail_component["metadata"]["failed_operations"], ["query:get"])
        self.assertEqual(durable.status_code, 200)
        recovered_component = next(
            component for component in recovered.json()["health"]["components"]
            if component["name"] == "durable_event_store"
        )
        self.assertTrue(recovered_component["healthy"])

    def test_event_detail_uses_exact_durable_lookup_and_requires_authentication(self):
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteEventRepository(Path(tmp) / "events.db")
            repository.record({"event_id": "event-1", "alert_state": "provisional"})
            repository.record({"event_id": "event-1", "alert_state": "confirmed"})
            repository.record({"event_id": "event-10", "alert_state": "dismissed"})
            client = TestClient(create_app(event_repository=repository))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                self.assertEqual(client.get("/api/events/event-1").status_code, 401)
                found = client.get("/api/events/event-1", headers={"X-API-Key": _KEY})
                missing = client.get("/api/events/event-unknown", headers={"X-API-Key": _KEY})
            repository.close()

        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["event"]["alert_state"], "confirmed")
        self.assertEqual(missing.status_code, 404)

    def test_event_detail_falls_back_to_masked_legacy_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                "\n".join([
                    '2026-06-16 10:00:00 | INFO | EVENT | '
                    '{"event_id":"legacy-1","alert_state":"provisional"}',
                    '2026-06-16 10:00:01 | INFO | EVENT | '
                    '{"event_id":"legacy-1","alert_state":"confirmed",'
                    '"description":"rtsp://admin:secret123@10.0.0.5/s"}',
                ]) + "\n",
                encoding="utf-8",
            )
            client = TestClient(create_app())
            with patch.object(service, "_ALERTS_LOG", log), patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                response = client.get("/api/events/legacy-1", headers={"X-API-Key": _KEY})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["event"]["alert_state"], "confirmed")
        self.assertNotIn("secret123", response.json()["event"]["description"])

    def test_event_detail_reports_unavailable_event_storage(self):
        class BrokenRepository:
            @staticmethod
            def get(_event_id):
                raise OSError("database unavailable")

        with tempfile.TemporaryDirectory() as tmp:
            absent_log = Path(tmp) / "alerts.log"
            client = TestClient(create_app(event_repository=BrokenRepository()))
            with patch.object(service, "_ALERTS_LOG", absent_log), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ):
                response = client.get("/api/events/event-1", headers={"X-API-Key": _KEY})

        self.assertEqual(response.status_code, 503)

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
        self.assertIn(r.json()["status"], {"healthy", "unhealthy"})
        self.assertNotIn("components", r.json())

    def test_compat_ready_unauth(self):
        r = self.client.get("/health/ready")
        self.assertIn(r.status_code, (200, 503))
        self.assertIn("status", r.json())

    def test_compat_ready_requires_camera_frame_when_pipeline_is_mounted(self):
        pipeline = Mock()
        pipeline.get_status.return_value = {
            "cameras": {
                "cam_001": {
                    "connected": True,
                    "is_stale": False,
                    "seconds_since_last_frame": None,
                    "consecutive_failures": 0,
                }
            },
            "models": {},
        }
        client = TestClient(create_app(pipeline=pipeline))

        response = client.get("/health/ready")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")


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

            frame = None
            try:
                # 첫 프레임을 먼저 소비해 stream이 기존 파일의 끝 offset을 고정한 뒤
                # 동기 append한다. scheduler race 없이 "신규 이벤트만 push" 계약을 검증한다.
                self.assertEqual(await asyncio.wait_for(gen.__anext__(), timeout=5), ": connected\n\n")
                with open(log, "a", encoding="utf-8") as f:
                    f.write(
                        '2026-06-17 10:00:05 | INFO | EVENT | '
                        '{"event_id":"ev-new","event_type":"fire","camera_id":"b",'
                        '"description":"rtsp://admin:secret123@10.0.0.5/s"}\n'
                    )
                for _ in range(5):
                    f = await asyncio.wait_for(gen.__anext__(), timeout=5)
                    # SSE cursor가 발급된 event는 "id:" line으로 시작한다.
                    if "event: alert" in f:
                        frame = f
                        break
            finally:
                await gen.aclose()

        self.assertIsNotNone(frame, "신규 이벤트의 alert 프레임이 push되지 않음")
        assert frame is not None
        self.assertIn("id: ", frame)                    # event_id가 있으면 SSE cursor가 발급됨
        self.assertIn('"event_type": "fire"', frame)  # 신규 이벤트만
        self.assertNotIn("old", frame)                # 초기 라인은 skip
        self.assertNotIn("secret123", frame)          # 마스킹 유지

    async def test_stream_replays_events_after_last_event_id(self):
        """Last-Event-ID가 있으면 cursor 이후 기록을 시간순 replay한다."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                "\n".join([
                    '2026-06-17 10:00:00 | INFO | EVENT | {"event_id":"ev-1","event_type":"old","camera_id":"a"}',
                    '2026-06-17 10:00:01 | INFO | EVENT | {"event_id":"ev-2","event_type":"fire","camera_id":"b"}',
                ]) + "\n",
                encoding="utf-8",
            )

            async def never_disconnected() -> bool:
                return False

            gen = service.stream_events(
                never_disconnected,
                alerts_log=log,
                poll_interval=0.02,
                heartbeat_every=10_000,
                last_event_id="ev-1",
            )
            try:
                self.assertEqual(await gen.__anext__(), ": connected\n\n")
                replay = await gen.__anext__()
            finally:
                await gen.aclose()

        self.assertIn("id: ev-2", replay)
        self.assertIn('"event_type": "fire"', replay)
        self.assertNotIn('"event_type": "old"', replay)

    async def test_stream_requests_reconciliation_for_unknown_cursor(self):
        """rotation/retention으로 cursor가 사라지면 REST reconciliation marker를 보낸다."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                '2026-06-17 10:00:00 | INFO | EVENT | {"event_id":"ev-1","event_type":"old","camera_id":"a"}\n',
                encoding="utf-8",
            )

            async def never_disconnected() -> bool:
                return False

            gen = service.stream_events(
                never_disconnected,
                alerts_log=log,
                poll_interval=0.02,
                heartbeat_every=10_000,
                last_event_id="expired-cursor",
            )
            try:
                self.assertEqual(await gen.__anext__(), ": connected\n\n")
                marker = await gen.__anext__()
            finally:
                await gen.aclose()

        self.assertIn("event: reconciliation-required", marker)
        self.assertIn("cursor-unavailable", marker)

    async def test_stream_replays_sqlite_lifecycle_records_with_monotonic_cursor(self):
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteEventRepository(Path(tmp) / "events.db")
            repository.record({"event_id": "ev-1", "event_type": "candidate", "timestamp": "2026-07-25T10:00:00Z"})
            repository.record({"event_id": "ev-1", "event_type": "confirmed", "timestamp": "2026-07-25T10:00:01Z"})

            async def never_disconnected() -> bool:
                return False

            gen = service.stream_events(
                never_disconnected,
                event_repository=repository,
                last_event_id="1",
                poll_interval=0.02,
                heartbeat_every=10_000,
            )
            try:
                self.assertEqual(await gen.__anext__(), ": connected\n\n")
                replay = await gen.__anext__()
            finally:
                await gen.aclose()
                repository.close()

        self.assertIn("id: 2", replay)
        self.assertIn('"event_type": "confirmed"', replay)

    async def test_durable_stream_failure_updates_health_status(self):
        class BrokenStreamRepository:
            @staticmethod
            def latest_stream_id():
                raise OSError("stream unavailable")

        async def never_disconnected() -> bool:
            return False

        status = service.DurableEventStoreStatus()
        gen = service.stream_events(
            never_disconnected,
            event_repository=BrokenStreamRepository(),
            durable_store_status=status,
        )
        try:
            self.assertEqual(await gen.__anext__(), ": connected\n\n")
            marker = await gen.__anext__()
        finally:
            await gen.aclose()

        self.assertIn("durable-store-unavailable", marker)
        self.assertEqual(status.health_check().metadata["failed_operations"], ["query:stream"])

    async def test_stream_marks_pruned_sqlite_cursor_for_reconciliation(self):
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteEventRepository(Path(tmp) / "events.db")
            repository.record({"event_id": "ev-1", "event_type": "old", "timestamp": "2026-07-25T10:00:00Z"})
            repository.record({"event_id": "ev-2", "event_type": "new", "timestamp": "2026-07-25T10:00:01Z"})
            with repository._connect() as connection:
                connection.execute("DELETE FROM event_records WHERE stream_id = 1")

            async def never_disconnected() -> bool:
                return False

            gen = service.stream_events(
                never_disconnected,
                event_repository=repository,
                last_event_id="0",
                poll_interval=0.02,
                heartbeat_every=10_000,
            )
            try:
                self.assertEqual(await gen.__anext__(), ": connected\n\n")
                marker = await gen.__anext__()
            finally:
                await gen.aclose()
                repository.close()

        self.assertIn("event: reconciliation-required", marker)
        self.assertIn("cursor-expired", marker)

    async def test_websocket_stream_replays_after_a_captured_durable_cursor(self):
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteEventRepository(Path(tmp) / "events.db")
            repository.record({"event_id": "ev-1", "event_type": "candidate", "timestamp": "2026-07-25T10:00:00Z"})
            repository.record({"event_id": "ev-2", "event_type": "confirmed", "timestamp": "2026-07-25T10:00:01Z"})

            async def never_disconnected() -> bool:
                return False

            cursor, reason = service.prepare_durable_event_cursor(repository, "1")
            gen = service.stream_events_ws(
                never_disconnected,
                event_repository=repository,
                cursor=cursor,
                reconciliation_reason=reason,
                poll_interval=0.02,
                heartbeat_every=10_000,
            )
            try:
                replay = await gen.__anext__()
            finally:
                await gen.aclose()
                repository.close()

        self.assertEqual(cursor, 1)
        self.assertIsNone(reason)
        self.assertEqual(replay["type"], "event")
        self.assertEqual(replay["stream_id"], 2)
        self.assertEqual(replay["data"]["event_type"], "confirmed")

    async def test_websocket_stream_marks_an_expired_durable_cursor(self):
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteEventRepository(Path(tmp) / "events.db")
            repository.record({"event_id": "ev-1", "event_type": "old", "timestamp": "2026-07-25T10:00:00Z"})
            repository.record({"event_id": "ev-2", "event_type": "new", "timestamp": "2026-07-25T10:00:01Z"})
            with repository._connect() as connection:
                connection.execute("DELETE FROM event_records WHERE stream_id = 1")

            async def never_disconnected() -> bool:
                return False

            cursor, reason = service.prepare_durable_event_cursor(repository, "0")
            gen = service.stream_events_ws(
                never_disconnected,
                event_repository=repository,
                cursor=cursor,
                reconciliation_reason=reason,
            )
            try:
                marker = await gen.__anext__()
            finally:
                await gen.aclose()
                repository.close()

        self.assertEqual(marker, {"type": "reconciliation-required", "reason": "cursor-expired"})


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
        self.assertIsInstance(kwargs["file_path"], Path)
        self.assertFalse(kwargs["file_path"].exists())
        self.assertNotIn("file_bytes", kwargs)

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

    def test_upload_rejects_concurrent_validation_before_reading_into_memory(self):
        class BusyLock:
            @staticmethod
            def locked():
                return True

        self.client.app.state.model_upload_lock = BusyLock()
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}), \
                patch.object(service, "handle_model_upload") as handler:
            response = self._post()
        self.assertEqual(response.status_code, 429)
        handler.assert_not_called()


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
                self.assertEqual(
                    client.get(
                        "/api/captures/20260617/cam_001/person_detected_103045.jpg"
                    ).status_code,
                    401,
                )
                self.assertEqual(r2.headers["cache-control"], "no-store")


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
class TestCameraProfiles(unittest.TestCase):
    """카메라 탐지 프로파일 — 읽기·도메인/이벤트 검증 저장·엔드포인트."""

    def _setup(self, tmp):
        import yaml
        cp = Path(tmp) / "camera_profiles.yaml"
        cp.write_text(yaml.safe_dump({"cameras": {
            "cam_001": {"domain": "general", "enabled": True, "enabled_events": ["person_detected", "fire"]}
        }}), encoding="utf-8")
        pol = Path(tmp) / "event_policies.yaml"
        pol.write_text(yaml.safe_dump({"catalog": {
            "person_detected": {"status": "active", "runtime": "detection"},
            "fire": {"status": "active", "runtime": "detection"},
            "fall": {"status": "experimental", "runtime": "detection"},
            "no_helmet": {"status": "active", "runtime": "detection"},
        }, "profiles": {
            "general": {"policies": {
                "person_detected": {"enabled": True, "alert_level": "low"},
                "fire": {"enabled": True, "alert_level": "critical"},
                "fall": {"enabled": True, "alert_level": "high"},
            }},
            "construction": {"policies": {"no_helmet": {"enabled": True, "alert_level": "high"}}},
        }}), encoding="utf-8")
        return cp, pol

    def test_get_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, _ = self._setup(tmp)
            res = service.get_camera_profiles(profiles_path=cp)
        self.assertEqual(res["cameras"]["cam_001"]["domain"], "general")
        self.assertIn("fire", res["cameras"]["cam_001"]["enabled_events"])

    def test_set_validates_and_filters(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, pol = self._setup(tmp)
            # construction으로 변경 — 도메인이 다르더라도 no_helmet은 카탈로그에 있으므로 허용.
            res = service.set_camera_profile("cam_001", "construction",
                                             ["no_helmet"], profiles_path=cp, policies_path=pol)
            self.assertEqual(res["domain"], "construction")
            self.assertEqual(res["enabled_events"], ["no_helmet"])
            # 영속
            self.assertEqual(service.get_camera_profiles(profiles_path=cp)["cameras"]["cam_001"]["domain"], "construction")
            # 알 수 없는 이벤트는 조용히 드롭하지 않고 ValueError로 거부 (개선: 침묵 실패 방지).
            with self.assertRaises(ValueError):
                service.set_camera_profile("cam_001", "construction",
                                           ["nonsense_event"], profiles_path=cp, policies_path=pol)

    def test_set_unknown_domain_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, pol = self._setup(tmp)
            with self.assertRaises(ValueError):
                service.set_camera_profile("cam_001", "atlantis", [], profiles_path=cp, policies_path=pol)

    def test_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            cp, pol = self._setup(tmp)
            client = TestClient(create_app())
            with patch.object(service, "_CAMERA_PROFILES", cp), \
                    patch.object(service, "_EVENT_POLICIES", pol), \
                    patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                h = {"X-API-Key": _KEY}
                self.assertEqual(client.get("/api/camera-profiles", headers=h).json()["cameras"]["cam_001"]["domain"], "general")
                r = client.put("/api/cameras/cam_001/profile", headers=h,
                json={"domain": "general", "enabled_events": ["fire"]})
                self.assertEqual(r.status_code, 200, r.text)
                self.assertEqual(r.json()["enabled_events"], ["fire"])
                self.assertEqual(client.put("/api/cameras/cam_001/profile", headers=h,
                                            json={"domain": "nope", "enabled_events": []}).status_code, 400)
                self.assertEqual(client.get("/api/camera-profiles").status_code, 401)

    def test_event_catalog_returns_union_of_defined_events(self):
        """이벤트 카탈로그는 모든 프로파일에 정의된 사건의 합(중복 제거)을 반환."""
        cat = service.get_event_catalog()
        self.assertIn("catalog", cat)
        names = {item["name"] for item in cat["catalog"]}
        # 일반/건설/학교/공장/주차장 사건이 모두 노출되어야 한다.
        for expected in {"fall", "fire", "smoke", "crowding", "no_helmet", "fight", "restricted_area", "camera_disconnect"}:
            self.assertIn(expected, names, f"카탈로그에 {expected} 누락")
        # 각 이벤트는 정의된 도메인 목록을 갖고, 도메인 별로 중복되지 않는다.
        for item in cat["catalog"]:
            self.assertIsInstance(item.get("domains"), list)
        by_name = {item["name"]: item for item in cat["catalog"]}
        self.assertTrue(by_name["fire"]["selectable"])
        self.assertTrue(by_name["fire"]["release_qualified"])
        self.assertFalse(by_name["fall"]["release_qualified"])
        self.assertFalse(by_name["fall"]["selectable"])
        self.assertTrue(by_name["fall"]["blocked_reason"])
        self.assertTrue(by_name["camera_disconnect"]["selectable"])
        self.assertEqual(by_name["camera_disconnect"]["runtime"], "health")
        self.assertFalse(by_name["camera_recovered"]["selectable"])
        self.assertEqual(by_name["loitering"]["runtime"], "tracking")
        self.assertEqual(by_name["loitering"]["requires_zone"], "loitering")
        self.assertEqual(by_name["loitering"]["tracking"]["dwell_time_sec"], 60)
        self.assertEqual(by_name["line_crossing"]["tracking"]["min_displacement_px"], 2)
        self.assertIn("general", cat["profiles"])

    def test_event_zone_warnings_validate_tracking_zone_type(self):
        warnings = service.event_zone_warnings(["line_crossing", "loitering"], [])
        self.assertEqual(len(warnings), 2)

        warnings = service.event_zone_warnings(
            ["line_crossing", "loitering"],
            [
                {"id": "line", "type": "polygon"},
                {"id": "loitering", "type": "polygon"},
            ],
        )
        self.assertEqual(warnings, ["line_crossing: 'line' zone must be line"])

    def test_endpoint_returns_catalog(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.get("/api/event-catalog", headers={"X-API-Key": _KEY})
            self.assertEqual(r.status_code, 200)
            self.assertIn("catalog", r.json())
            names = {item["name"] for item in r.json()["catalog"]}
            self.assertIn("fall", names)
        self.assertIn("fire", names)

    def test_mutable_yaml_cannot_make_fall_selectable(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, pol = self._setup(tmp)
            import yaml

            policy = yaml.safe_load(pol.read_text(encoding="utf-8"))
            policy["catalog"]["fall"]["status"] = "active"
            policy["catalog"]["fall"]["selectable"] = True
            pol.write_text(yaml.safe_dump(policy), encoding="utf-8")

            catalog = service.get_event_catalog(pol)
            fall = next(item for item in catalog["catalog"] if item["name"] == "fall")
            self.assertFalse(fall["release_qualified"])
            self.assertFalse(fall["selectable"])
            with self.assertRaisesRegex(ValueError, "사용할 수 없는"):
                service.validate_event_selection("general", ["fall"], pol)

    def test_legacy_profile_update_applies_to_mounted_pipeline(self):
        class Manager:
            @staticmethod
            def get_camera(camera_id):
                return object() if camera_id == "cam_001" else None

        class Pipeline:
            def __init__(self):
                self.camera_manager = Manager()
                self._camera_profiles = {"cam_001": {"zones": {"restricted": {}}}}
                self.applied_profile = None

            def set_camera_profile(self, _camera_id, profile):
                self.applied_profile = profile

        with tempfile.TemporaryDirectory() as tmp:
            cp, pol = self._setup(tmp)
            pipeline = Pipeline()
            client = TestClient(create_app(pipeline=pipeline))
            with patch.object(service, "_CAMERA_PROFILES", cp), \
                    patch.object(service, "_EVENT_POLICIES", pol), \
                    patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                response = client.put(
                    "/api/cameras/cam_001/profile",
                    headers={"X-API-Key": _KEY},
                    json={"domain": "construction", "enabled_events": ["no_helmet"]},
                )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["pipeline_applied"])
        self.assertEqual(pipeline.applied_profile["enabled_events"], ["no_helmet"])
        self.assertIn("zones", pipeline.applied_profile)


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestEventsWebSocket(unittest.TestCase):
    """이벤트 실시간 스트림 WebSocket — 인증, 초기 이벤트, ping/pong."""

    def test_ws_requires_auth(self):
        """인증 없이 /ws/events 연결 시 4401 close."""
        client = TestClient(create_app())
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}), self.assertRaises(
            WebSocketDisconnect
        ) as ctx, client.websocket_connect("/ws/events") as ws:
            ws.receive_json()
        self.assertEqual(ctx.exception.code, 4401)

    def test_ws_sends_initial_events_with_key(self):
        """인증 후 연결 시 초기 이벤트 목록 수신."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_type":"fire","camera_id":"cam1"}\n',
                encoding="utf-8",
            )
            client = TestClient(create_app())
            with patch.object(service, "_ALERTS_LOG", log), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ), client.websocket_connect(
                "/ws/events", headers={"X-API-Key": _KEY}
            ) as ws:
                first = ws.receive_json()
                self.assertEqual(first["type"], "initial")
                self.assertEqual(len(first["events"]), 1)
                self.assertEqual(first["events"][0]["event_type"], "fire")

    def test_ws_replays_durable_records_from_last_event_id(self):
        from src.storage.sqlite_events import SQLiteEventRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = SQLiteEventRepository(Path(tmp) / "events.db")
            repository.record({"event_id": "ev-1", "event_type": "candidate", "timestamp": "2026-07-25T10:00:00Z"})
            repository.record({"event_id": "ev-2", "event_type": "confirmed", "timestamp": "2026-07-25T10:00:01Z"})
            client = TestClient(create_app(event_repository=repository))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}), client.websocket_connect(
                "/ws/events", headers={"X-API-Key": _KEY, "Last-Event-ID": "1"}
            ) as ws:
                initial = ws.receive_json()
                replay = ws.receive_json()
            repository.close()

        self.assertEqual(initial["type"], "initial")
        self.assertEqual(initial["cursor"], 1)
        self.assertEqual(initial["events"], [])
        self.assertEqual(replay["type"], "event")
        self.assertEqual(replay["stream_id"], 2)
        self.assertEqual(replay["data"]["event_type"], "confirmed")

    def test_ws_ping_pong(self):
        """ping 메시지에 pong 응답."""
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "alerts.log"
            log.write_text(
                '2026-06-16 10:00:00 | INFO | EVENT | {"event_type":"fire","camera_id":"cam1"}\n',
                encoding="utf-8",
            )
            client = TestClient(create_app())
            with patch.object(service, "_ALERTS_LOG", log), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ), client.websocket_connect(
                "/ws/events", headers={"X-API-Key": _KEY}
            ) as ws:
                # 초기 이벤트 소비
                ws.receive_json()
                ws.send_json({"type": "ping"})
                # pong 또는 heartbeat 수신
                for _ in range(5):
                    msg = ws.receive_json()
                    if msg.get("type") in {"pong", "heartbeat"}:
                        break
                else:
                    self.fail("pong/heartbeat 응답을 받지 못했습니다")
                self.assertIn(msg.get("type"), {"pong", "heartbeat"})

    def test_ws_rejects_api_key_in_query_string(self):
        client = TestClient(create_app())
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}), self.assertRaises(
            WebSocketDisconnect
        ) as ctx, client.websocket_connect(f"/ws/events?key={_KEY}") as ws:
            ws.receive_json()
        self.assertEqual(ctx.exception.code, 4401)


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestCameraTelemetry(unittest.TestCase):
    """카메라 라이브 텔레메트리 — 데몬 파이프라인 mount 시 카메라별 상태 노출."""

    class _FakePipe:
        def get_status(self):
            return {
                "cameras": {
                    "cam_001": {"connected": True, "fps": 25.0, "dropped_frames": 3, "is_stale": False},
                }
            }

    def test_with_pipeline(self):
        client = TestClient(create_app(pipeline=self._FakePipe()))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            r = client.get("/api/cameras/telemetry", headers={"X-API-Key": _KEY})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["connected"])
            self.assertEqual(body["telemetry_status"], "ok")
            self.assertTrue(body["generated_at"].endswith("Z"))
            self.assertEqual(body["cameras"]["cam_001"]["fps"], 25.0)
            self.assertFalse(body["cameras"]["cam_001"]["is_stale"])
            self.assertEqual(client.get("/api/cameras/telemetry").status_code, 401)  # 미인증

    def test_without_pipeline(self):
        client = TestClient(create_app())  # standalone(파이프라인 없음)
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            body = client.get("/api/cameras/telemetry", headers={"X-API-Key": _KEY}).json()
            self.assertFalse(body["connected"])
            self.assertEqual(body["telemetry_status"], "unavailable")
            self.assertTrue(body["generated_at"].endswith("Z"))
            self.assertEqual(body["cameras"], {})

    def test_pipeline_status_error_is_not_reported_as_connected(self):
        class BrokenPipe:
            def get_status(self):
                raise RuntimeError("pipeline unavailable")

        client = TestClient(create_app(pipeline=BrokenPipe()))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            body = client.get("/api/cameras/telemetry", headers={"X-API-Key": _KEY}).json()
        self.assertFalse(body["connected"])
        self.assertEqual(body["telemetry_status"], "error")
        self.assertEqual(body["cameras"], {})


@unittest.skipUnless(_HAS_API, "fastapi/httpx 미설치 — skip")
class TestYamlCommentPreservation(unittest.TestCase):
    """설정 쓰기 시 yaml 주석 보존(ruamel round-trip) — 기존 safe_dump 주석 소실 한계 해소."""

    def test_alert_prefs_save_preserves_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "alert_preferences.yaml"
            p.write_text(
                "# 중요 안내: SMTP는 환경변수로 주입\n"
                "email_alert_level_threshold: medium  # 발송 임계값\n"
                "per_event_email_enabled: {}\n",
                encoding="utf-8",
            )
            service.set_alert_preferences("high", {"fire": True}, prefs_path=p)
            raw = p.read_text(encoding="utf-8")
            # 기능적으로도 정상(p가 살아있는 동안 다시 읽어 값 확인)
            reread = service.get_alert_preferences(prefs_path=p)["email_alert_level_threshold"]
        self.assertIn("# 중요 안내: SMTP는 환경변수로 주입", raw)  # 헤더 주석 보존
        self.assertIn("# 발송 임계값", raw)  # 인라인 주석 보존
        self.assertIn("high", raw)  # 값 변경 반영
        self.assertEqual(reread, "high")

    def test_model_enabled_save_preserves_comments(self):
        import yaml as _y

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "model_profiles.yaml"
            p.write_text(
                "# 모델 프로파일 — 라이선스 경고 등 중요 주석\n"
                + _y.safe_dump({"models": {"m1": {"enabled": False, "type": "yolo_onnx"}}}),
                encoding="utf-8",
            )
            service.set_model_enabled("m1", True, profiles_path=p)
            raw = p.read_text(encoding="utf-8")
        self.assertIn("# 모델 프로파일 — 라이선스 경고", raw)  # 주석 보존
        self.assertIn("enabled: true", raw)  # 값 변경 반영


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

    def test_system_propagates_sanitized_ai_runtime(self):
        class Pipeline:
            def get_ai_runtime_status(self):
                return {"vlm": {"configured_enabled": True, "queue": {"saturated": False}}}
        client = TestClient(create_app(pipeline=Pipeline()))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            response = client.get("/api/system", headers={"X-API-Key": _KEY})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ai_runtime"]["vlm"]["configured_enabled"])
        self.assertNotIn("digest", response.text.lower())
        self.assertNotIn("endpoint", response.text.lower())


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

    def test_delivery_status_requires_auth_and_uses_runtime_manager(self):
        class AlertManager:
            def notification_status(self):
                return {
                    "email_configured": True,
                    "recipient_count": 2,
                    "outbox": {"running": True, "pending": 1, "inflight": 0, "delivered": 4, "dead_letter": 0},
                }

        client = TestClient(create_app(alert_manager=AlertManager()))
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
            self.assertEqual(client.get("/api/alerts/status").status_code, 401)
            response = client.get("/api/alerts/status", headers={"X-API-Key": _KEY})

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["recipient_count"], 2)
        self.assertEqual(response.json()["outbox"]["pending"], 1)


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
        self.assertEqual(items[0]["actor_type"], "api-key")
        self.assertEqual(items[0]["outcome"], "success")
        self.assertEqual(items[0]["status_code"], 200)

    def test_failed_mutation_is_audited_with_outcome_and_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "audit.jsonl"
            client = TestClient(create_app())
            with patch.object(service, "_AUDIT_LOG", p), patch.dict(os.environ, {"KSI_CCTV_API_KEY": _KEY}):
                response = client.put(
                    "/api/alert-preferences",
                    headers={"X-API-Key": _KEY},
                    json={"email_alert_level_threshold": "not-a-level"},
                )
                self.assertEqual(response.status_code, 400)
                entry = service.list_audit(audit_log=p)[0]

        self.assertEqual(entry["event"], "api_mutation_failed")
        self.assertEqual(entry["actor_type"], "api-key")
        self.assertEqual(entry["outcome"], "failure")
        self.assertEqual(entry["status_code"], 400)
        self.assertEqual(entry["request_path"], "/api/alert-preferences")

    def test_list_masks_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "audit.jsonl"
            service.write_audit("model_upload_failed", audit_log=p, error="rtsp://admin:secret999@h/s")
            items = service.list_audit(audit_log=p)
        self.assertNotIn("secret999", items[0]["error"])

    def test_audit_entries_have_verifiable_hash_chain_and_signatures(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            self.assertTrue(service.write_audit("first", audit_log=path))
            self.assertTrue(service.write_audit("second", audit_log=path))
            entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(service.audit_public_key(path))
            )

        self.assertIsNone(entries[0]["previous_hash"])
        self.assertEqual(entries[1]["previous_hash"], entries[0]["entry_hash"])
        for entry in entries:
            unsigned = dict(entry)
            signature = base64.b64decode(unsigned.pop("signature"))
            entry_hash = unsigned.pop("entry_hash")
            self.assertEqual(
                entry_hash,
                hashlib.sha256(service.canonical_json_bytes(unsigned)).hexdigest(),
            )
            public_key.verify(signature, bytes.fromhex(entry_hash))

    def test_audit_identity_files_are_complete_restricted_and_consistent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            public_value = service.audit_public_key(path)
            private_path, public_path = service._audit_key_paths(path)

            self.assertEqual(private_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(public_path.stat().st_mode & 0o777, 0o644)
            self.assertEqual(public_path.read_text(encoding="ascii").strip(), public_value)
            self.assertEqual(len(private_path.read_bytes()), 32)

    def test_audit_identity_fails_closed_when_partial_or_missing_for_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            partial = Path(tmp) / "partial.jsonl"
            private_path, _public_path = service._audit_key_paths(partial)
            private_path.write_bytes(os.urandom(32))
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                service.audit_public_key(partial)

            historical = Path(tmp) / "historical.jsonl"
            historical.write_text('{"legacy":true}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "history exists"):
                service.audit_public_key(historical)
            self.assertFalse(service.write_audit("must-not-rekey", audit_log=historical))
            historical_private, historical_public = service._audit_key_paths(historical)
            self.assertFalse(historical_private.exists())
            self.assertFalse(historical_public.exists())

    def test_audit_identity_rejects_a_mismatched_public_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            service.audit_public_key(path)
            _private_path, public_path = service._audit_key_paths(path)
            public_path.write_text(base64.b64encode(os.urandom(32)).decode("ascii") + "\n", encoding="ascii")

            with self.assertRaisesRegex(RuntimeError, "does not match"):
                service.audit_public_key(path)

    def test_audit_rotation_is_bounded_and_preserves_a_verifiable_anchor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            with patch.object(service, "_AUDIT_MAX_BYTES", 512), patch.object(
                service, "_AUDIT_MAX_SEGMENTS", 2
            ):
                for index in range(8):
                    self.assertTrue(
                        service.write_audit(
                            f"entry-{index}", audit_log=path, detail="x" * 256
                        )
                    )
                entries, anchor = service.list_audit_window(100, path)

            self.assertTrue(path.is_file())
            self.assertTrue(path.with_name("audit.jsonl.1").is_file())
            self.assertTrue(path.with_name("audit.jsonl.2").is_file())
            self.assertFalse(path.with_name("audit.jsonl.3").exists())
            self.assertEqual(entries[0]["event"], "entry-7")
            self.assertIsInstance(anchor, str)
            previous = anchor
            for entry in reversed(entries):
                self.assertEqual(entry["previous_hash"], previous)
                previous = entry["entry_hash"]

    def test_audit_write_repairs_an_incomplete_tail_before_continuing_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            self.assertTrue(service.write_audit("first", audit_log=path))
            with path.open("ab") as handle:
                handle.write(b'{"interrupted":')
            self.assertTrue(service.write_audit("second", audit_log=path))
            entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

        self.assertEqual([entry["event"] for entry in entries], ["first", "second"])
        self.assertEqual(entries[1]["previous_hash"], entries[0]["entry_hash"])

    def test_audit_redacts_nested_secret_keys_and_values_before_storage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            service.write_audit(
                "nested",
                audit_log=path,
                metadata={"password": "open-sesame", "headers": {"Authorization": "Bearer abc123"}},
                detail="token=abc123",
            )
            stored = path.read_text(encoding="utf-8")

        self.assertNotIn("open-sesame", stored)
        self.assertNotIn("abc123", stored)
        self.assertIn("[REDACTED]", stored)

    def test_support_bundle_manifest_hashes_and_signature_verify(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            service.write_audit("bundle_fixture", audit_log=path)
            client = TestClient(create_app())
            with patch.object(service, "_AUDIT_LOG", path), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ):
                response = client.get("/api/support/bundle", headers={"X-API-Key": _KEY})

        self.assertEqual(response.status_code, 200, response.text)
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            for name, digest in manifest["files"].items():
                self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
        signature = base64.b64decode(manifest.pop("signature"))
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(manifest["audit_public_key"]))
        public_key.verify(signature, service.canonical_json_bytes(manifest))

    def test_support_bundle_keeps_a_verifiable_anchor_for_truncated_audit_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audit.jsonl"
            for index in range(201):
                self.assertTrue(service.write_audit(f"entry-{index}", audit_log=path))
            expected_anchor = json.loads(path.read_text(encoding="utf-8").splitlines()[0])["entry_hash"]
            client = TestClient(create_app())
            with patch.object(service, "_AUDIT_LOG", path), patch.dict(
                os.environ, {"KSI_CCTV_API_KEY": _KEY}
            ):
                response = client.get("/api/support/bundle", headers={"X-API-Key": _KEY})

        self.assertEqual(response.status_code, 200, response.text)
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            audit = json.loads(archive.read("audit.json"))
        self.assertEqual(len(audit["entries"]), 200)
        self.assertEqual(audit["entries"][0]["event"], "entry-200")
        self.assertEqual(audit["entries"][-1]["event"], "entry-1")
        self.assertEqual(audit["anchor_previous_hash"], expected_anchor)

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
