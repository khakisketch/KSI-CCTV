import json
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.app import create_app
from src.cameras import CameraConfig
from src.commissioning import CommissioningConflict, CommissioningStore
from src.utils.health import ComponentHealth, HealthChecker


def test_commissioning_store_is_atomic_private_and_revisioned():
    with tempfile.TemporaryDirectory() as tmp:
        store = CommissioningStore(Path(tmp))
        first = store.update_site(
            {"name": "Factory A", "expected_camera_count": 4},
            expected_revision=0,
        )
        assert first["revision"] == 1
        assert first["site"]["name"] == "Factory A"
        assert store.path.stat().st_mode & 0o777 == 0o600

        with pytest.raises(CommissioningConflict):
            store.update_site({"name": "stale"}, expected_revision=0)

        second = store.update_camera(
            "cam_001",
            {"live_verified": True, "notes": "front gate"},
            expected_revision=1,
        )
        assert second["revision"] == 2
        assert second["camera_checks"]["cam_001"]["live_verified"] is True
        assert store.backup_path.stat().st_mode & 0o777 == 0o600


def test_commissioning_store_completion_can_be_reopened():
    with tempfile.TemporaryDirectory() as tmp:
        store = CommissioningStore(Path(tmp))
        completed = store.mark_completed(expected_revision=0)
        assert completed["completed_at"]
        accepted = store.record_handoff("Customer", "accepted", expected_revision=1)
        assert accepted["handoff"]["customer_name"] == "Customer"
        assert accepted["handoff"]["accepted_at"]
        reopened = store.reopen(expected_revision=2)
        assert reopened["completed_at"] is None
        assert reopened["handoff"]["accepted_at"] is None


def test_commissioning_edit_invalidates_completion_and_handoff():
    with tempfile.TemporaryDirectory() as tmp:
        store = CommissioningStore(Path(tmp))
        store.mark_completed(expected_revision=0)
        store.record_handoff("Customer", "accepted", expected_revision=1)

        edited = store.update_site({"location": "Changed"}, expected_revision=2)

        assert edited["completed_at"] is None
        assert edited["handoff"]["accepted_at"] is None


def test_system_configuration_change_invalidates_completion_and_handoff():
    with tempfile.TemporaryDirectory() as tmp:
        store = CommissioningStore(Path(tmp))
        store.mark_completed(expected_revision=0)
        store.record_handoff("Customer", "accepted", expected_revision=1)

        invalidated = store.invalidate_completion()

        assert invalidated["revision"] == 3
        assert invalidated["completed_at"] is None
        assert invalidated["handoff"]["accepted_at"] is None


class _Pipeline:
    is_running = True
    detector = None
    model_registry = None

    @staticmethod
    def get_status():
        return {"running": True, "cameras": {}}

    @staticmethod
    def get_ai_runtime_status():
        return {"vlm": {"configured_enabled": False, "queue": {"saturated": False}}}


def _checker():
    checker = HealthChecker()
    checker.register("storage", lambda: (True, "storage ready"))
    return checker


def test_commissioning_api_persists_site_and_reports_actionable_failures():
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(
            camera_directory=Path(tmp),
            pipeline=_Pipeline(),
            mediamtx=object(),
            checker_factory=_checker,
        ))
        headers = {"X-API-Key": "commissioning-key"}
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            initial = client.get("/api/commissioning", headers=headers)
            assert initial.status_code == 200
            assert initial.json()["ready_to_complete"] is False
            assert any(check["id"] == "site" and check["status"] == "fail" for check in initial.json()["checks"])

            updated = client.put("/api/commissioning/site", headers=headers, json={
                "name": "Factory A",
                "location": "Seoul",
                "installer": "Installer",
                "expected_camera_count": 2,
                "expected_revision": 0,
            })
            assert updated.status_code == 200
            assert updated.json()["site"]["name"] == "Factory A"
            assert updated.json()["revision"] == 1

            stale = client.put("/api/commissioning/site", headers=headers, json={
                "name": "Stale",
                "expected_revision": 0,
            })
            assert stale.status_code == 409

            completion = client.post(
                "/api/commissioning/complete",
                headers=headers,
                json={"expected_revision": 1},
            )
            assert completion.status_code == 409
            assert "필수 검사" in completion.json()["detail"]

            report = client.get("/api/commissioning/report", headers=headers)
            assert report.status_code == 200
            assert report.headers["cache-control"] == "private, no-store"
            assert report.json()["site"]["name"] == "Factory A"
            assert "rtsp" not in report.text.lower()


def test_source_capabilities_and_runtime_catalog_are_explicit():
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(
            camera_directory=Path(tmp),
            pipeline=_Pipeline(),
            checker_factory=_checker,
        ))
        headers = {"X-API-Key": "commissioning-key"}
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            capabilities = client.get("/api/video-sources/capabilities", headers=headers)
            assert capabilities.status_code == 200
            adapters = {item["kind"]: item for item in capabilities.json()["adapters"]}
            assert adapters["rtsp"]["available"] is True
            assert adapters["v4l2"]["available"] is False

            catalog = client.get("/api/event-catalog", headers=headers)
            assert catalog.status_code == 200
            by_name = {item["name"]: item for item in catalog.json()["catalog"]}
            assert by_name["fire"]["selectable"] is False
            assert by_name["camera_disconnect"]["selectable"] is True


def test_handoff_acceptance_requires_completion_and_is_included_in_report():
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(
            camera_directory=Path(tmp),
            pipeline=_Pipeline(),
            checker_factory=_checker,
        ))
        headers = {"X-API-Key": "commissioning-key"}
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            premature = client.put("/api/commissioning/handoff", headers=headers, json={
                "customer_name": "Customer",
                "expected_revision": 0,
            })
            client.app.state.commissioning.mark_completed(expected_revision=0)
            accepted = client.put("/api/commissioning/handoff", headers=headers, json={
                "customer_name": "Customer",
                "notes": "Reviewed on site",
                "expected_revision": 1,
            })
            report = client.get("/api/commissioning/report", headers=headers)

        assert premature.status_code == 409
        assert accepted.status_code == 200
        assert accepted.json()["handoff"]["accepted_at"]
        assert report.json()["revision"] == 2
        assert report.json()["handoff"]["customer_name"] == "Customer"


def test_camera_update_invalidates_completed_handoff():
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(camera_directory=Path(tmp), checker_factory=_checker))
        headers = {"X-API-Key": "commissioning-key"}
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            created = client.post("/api/cameras", headers=headers, json={
                "id": "cam_001",
                "name": "Front gate",
                "rtsp_url": "rtsp://192.168.1.20/live",
                "enabled": False,
                "profile": "general",
                "enabled_events": [],
            })
            client.app.state.commissioning.mark_completed(expected_revision=0)
            client.app.state.commissioning.record_handoff(
                "Customer",
                "accepted",
                expected_revision=1,
            )
            updated = client.put(
                "/api/cameras/cam_001?expected_revision=1",
                headers=headers,
                json={
                    "name": "Front gate updated",
                    "rtsp_url": "rtsp://192.168.1.20/live",
                    "enabled": False,
                    "profile": "general",
                    "enabled_events": [],
                },
            )
            state = client.app.state.commissioning.read()

        assert created.status_code == 201
        assert updated.status_code == 200
        assert state["completed_at"] is None
        assert state["handoff"]["accepted_at"] is None


def test_support_bundle_is_downloadable_and_contains_only_redacted_operational_data():
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(
            camera_directory=Path(tmp),
            pipeline=_Pipeline(),
            checker_factory=_checker,
        ))
        headers = {"X-API-Key": "commissioning-key"}
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            response = client.get("/api/support/bundle", headers=headers)

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == {
                "manifest.json", "commissioning.json", "health.json", "audit.json",
            }
            content = "\n".join(archive.read(name).decode() for name in archive.namelist())
        assert "rtsp://" not in content
        assert "password" not in content.lower()
        assert "secret" not in content.lower()


def test_support_bundle_omits_commissioning_site_identity_and_location():
    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(
            camera_directory=Path(tmp),
            pipeline=_Pipeline(),
            checker_factory=_checker,
        ))
        headers = {"X-API-Key": "commissioning-key"}
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            client.app.state.commissioning.update_site(
                {"name": "Confidential Factory", "location": "Building 7, Restricted Campus"},
                expected_revision=0,
            )
            response = client.get("/api/support/bundle", headers=headers)

        assert response.status_code == 200
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            commissioning = archive.read("commissioning.json").decode("utf-8")
        assert '"site_configured": true' in commissioning
        assert '"site": {' not in commissioning
        assert "Confidential Factory" not in commissioning
        assert "Restricted Campus" not in commissioning


def test_support_bundle_keeps_safe_camera_reconnect_diagnostics():
    class PipelineWithReconnectStatus(_Pipeline):
        def get_status(self):
            return {
                "running": True,
                "cameras": {
                    "cam-1": {
                        "health_state": "receiving",
                        "fps": 12.0,
                        "dropped_frames": 3,
                        "seconds_since_last_frame": 0.4,
                        "reconnect_attempts": 5,
                        "consecutive_failures": 1,
                    }
                },
            }

    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(
            camera_directory=Path(tmp),
            pipeline=PipelineWithReconnectStatus(),
            checker_factory=_checker,
        ))
        headers = {"X-API-Key": "commissioning-key"}
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            client.app.state.camera_repository.create(
                CameraConfig(
                    id="cam-1",
                    name="Support test camera",
                    rtsp_url="rtsp://192.168.1.20/live",
                ),
                expected_revision=0,
            )
            response = client.get("/api/support/bundle", headers=headers)

        assert response.status_code == 200
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            commissioning = json.loads(archive.read("commissioning.json"))
        camera = commissioning["cameras"][0]
        assert camera["dropped_frames"] == 3
        assert camera["seconds_since_last_frame"] == 0.4
        assert camera["reconnect_attempts"] == 5
        assert camera["consecutive_failures"] == 1


def test_support_bundle_redacts_health_messages_and_metadata():
    def sensitive_checker():
        checker = HealthChecker()
        checker.register(
            "vlm",
            lambda: ComponentHealth(
                name="vlm",
                healthy=False,
                message="request failed at https://admin:credential@example.invalid/api",
                metadata={"last_error": "credential", "endpoint": "https://example.invalid"},
            ),
        )
        return checker

    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(
            camera_directory=Path(tmp),
            pipeline=_Pipeline(),
            checker_factory=sensitive_checker,
        ))
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            response = client.get("/api/support/bundle", headers={"X-API-Key": "commissioning-key"})

        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            health = archive.read("health.json").decode("utf-8")
        assert response.status_code == 200
        assert "credential" not in health
        assert "example.invalid" not in health
        assert '"unhealthy_component_count": 1' in health


def test_commissioning_and_support_bundle_propagate_sanitized_ai_runtime():
    class RuntimePipeline(_Pipeline):
        @staticmethod
        def get_ai_runtime_status():
            return {"vlm": {"configured_enabled": True, "queue": {"saturated": True}}}

    with tempfile.TemporaryDirectory() as tmp:
        client = TestClient(create_app(camera_directory=Path(tmp), pipeline=RuntimePipeline(), checker_factory=_checker))
        with patch.dict("os.environ", {"KSI_CCTV_API_KEY": "commissioning-key"}):
            commissioning = client.get("/api/commissioning", headers={"X-API-Key": "commissioning-key"})
            bundle = client.get("/api/support/bundle", headers={"X-API-Key": "commissioning-key"})
    assert commissioning.json()["event_runtime"]["ai_runtime"]["vlm"]["queue"]["saturated"] is True
    with zipfile.ZipFile(BytesIO(bundle.content)) as archive:
        health = archive.read("health.json").decode("utf-8")
        runtime = archive.read("commissioning.json").decode("utf-8")
    assert '"ai_runtime"' in health
    assert '"ai_runtime"' in runtime
    assert "digest" not in health.lower() + runtime.lower()
    assert "endpoint" not in health.lower() + runtime.lower()
