"""Installer camera domain tests: persistence, secrecy, SSRF, and API injection."""
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.app import create_app
from src.cameras import (
    CameraConfig,
    CameraInput,
    CameraLimitReached,
    CameraRepository,
    CameraZone,
    CredentialStore,
    MediaMTXClient,
    OnvifAdapter,
    OnvifClient,
    RevisionConflict,
    RtspProbe,
    WsDiscoveryClient,
    compile_plan,
)

KEY = "camera-test-key"


class TestCameraRepository(unittest.TestCase):
    def test_camera_input_rejects_rtsp_query_tokens(self):
        with self.assertRaises(ValueError):
            CameraConfig.from_input(CameraInput(
                id="token-camera",
                name="Token camera",
                rtsp_url="rtsp://192.168.1.20/live?token=secret",
            ))

    def test_public_camera_view_masks_legacy_rtsp_url_credentials(self):
        # Legacy persisted records can predate CameraInput's userinfo rejection.
        camera = CameraConfig(
            id="cam_legacy",
            name="Legacy",
            rtsp_url="rtsp://admin:secret123@192.168.1.10/live",
        )
        public = camera.public_dict()
        self.assertNotIn("secret123", public["stream_url"])
        self.assertIn("***", public["stream_url"])

    def test_line_zone_preserves_direction_and_requires_two_points(self):
        zone = CameraZone(
            id="gate",
            type="line",
            direction="negative",
            points=[{"x": 0.1, "y": 0.5}, {"x": 0.9, "y": 0.5}],
        )

        self.assertEqual(zone.direction, "negative")
        self.assertEqual(len(zone.points), 2)
        with self.assertRaises(ValueError):
            CameraZone(
                id="gate",
                type="line",
                points=[
                    {"x": 0.1, "y": 0.5},
                    {"x": 0.5, "y": 0.5},
                    {"x": 0.9, "y": 0.5},
                ],
            )

    def test_atomic_revision_conflict_and_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = CameraRepository(Path(tmp))
            first = CameraConfig(id="one", name="One", rtsp_url="rtsp://192.168.2.10/live")
            revision, _ = repo.save(first, expected_revision=0)
            self.assertEqual(revision, 1)
            with self.assertRaises(RevisionConflict):
                repo.save(first, expected_revision=0)
            second = CameraConfig(id="two", name="Two", rtsp_url="rtsp://192.168.2.11/live")
            revision, _ = repo.save(second, expected_revision=1)
            self.assertEqual(revision, 2)
            self.assertTrue(repo.backup_path.exists())
            self.assertEqual(repo.backup_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual([c.id for c in repo.list()[1]], ["one", "two"])

    def test_atomic_profile_update_preserves_other_camera_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = CameraRepository(Path(tmp))
            camera = CameraConfig(
                id="one",
                name="Original",
                location="Gate",
                rtsp_url="rtsp://192.168.2.10/live",
            )
            repo.save(camera, expected_revision=0)
            repo.save(camera.model_copy(update={"name": "Concurrent edit"}), expected_revision=1)

            _revision, updated = repo.update_profile("one", "general", ["fire"])

            self.assertEqual(updated.name, "Concurrent edit")
            self.assertEqual(updated.location, "Gate")
            self.assertEqual(updated.enabled_events, ["fire"])

    def test_profile_update_rejects_stale_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = CameraRepository(Path(tmp))
            camera = CameraConfig(id="one", name="Original", rtsp_url="rtsp://192.168.2.10/live")
            repo.save(camera, expected_revision=0)
            repo.update_profile("one", "general", ["fire"], expected_revision=1)

            with self.assertRaises(RevisionConflict):
                repo.update_profile("one", "general", ["smoke"], expected_revision=1)

    def test_stale_full_update_cannot_overwrite_newer_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = CameraRepository(Path(tmp))
            camera = CameraConfig(id="one", name="Original", rtsp_url="rtsp://192.168.2.10/live")
            repo.save(camera, expected_revision=0)
            observed_revision, observed = repo.list()
            repo.update_profile("one", "general", ["fire"])

            with self.assertRaises(RevisionConflict):
                repo.save(
                    observed[0].model_copy(update={"name": "Stale full update"}),
                    expected_revision=observed_revision,
                )

    def test_plan_is_deterministic_and_excludes_credentials(self):
        plan = compile_plan(4, [
            CameraConfig(id="z", name="Z", rtsp_url="rtsp://192.168.1.3/z", credential_ref="camera:z"),
            CameraConfig(id="a", name="A", rtsp_url="rtsp://192.168.1.2/a", zone="loading"),
        ])
        self.assertEqual([c["id"] for c in plan.cameras], ["a", "z"])
        self.assertNotIn("credential_ref", str(plan.to_dict()))

    def test_create_enforces_limit_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = CameraRepository(Path(tmp))
            for index in range(repo.MAX_CAMERAS):
                repo.create(CameraConfig(
                    id=f"cam-{index}", name="Camera", rtsp_url="rtsp://192.168.1.10/live"
                ), expected_revision=None)
            with self.assertRaises(CameraLimitReached) as error:
                repo.create(CameraConfig(
                    id="cam-over", name="Camera", rtsp_url="rtsp://192.168.1.10/live"
                ), expected_revision=None)
            self.assertIn("limit", str(error.exception))
            self.assertEqual(len(repo.list()[1]), repo.MAX_CAMERAS)

    def test_combined_legacy_managed_limit_is_atomic_for_concurrent_creates(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = CameraRepository(Path(tmp))
            occupied = {f"legacy-{index}" for index in range(15)}
            barrier = threading.Barrier(2)
            outcomes: list[str] = []

            def create(camera_id: str) -> None:
                barrier.wait()
                try:
                    repo.create(
                        CameraConfig(
                            id=camera_id,
                            name=camera_id,
                            rtsp_url="rtsp://192.168.1.20/live",
                        ),
                        expected_revision=None,
                        occupied_ids=occupied,
                    )
                    outcomes.append("created")
                except CameraLimitReached:
                    outcomes.append("limited")

            threads = [
                threading.Thread(target=create, args=("managed-one",)),
                threading.Thread(target=create, args=("managed-two",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

            self.assertEqual(sorted(outcomes), ["created", "limited"])
            self.assertEqual(len(occupied | {camera.id for camera in repo.list()[1]}), 16)


class TestCredentialStore(unittest.TestCase):
    def test_credentials_are_encrypted_and_owner_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = CredentialStore(Path(tmp))
            store.put("camera:one", "installer", "not-in-json", "554|192.168.1.20")
            self.assertEqual(store.get("camera:one")["password"], "not-in-json")
            self.assertEqual(store.get("camera:one")["target_scope"], "554|192.168.1.20")
            self.assertNotIn(b"not-in-json", store.data_path.read_bytes())
            self.assertNotIn(b"192.168.1.20", store.data_path.read_bytes())
            self.assertEqual(stat.S_IMODE(store.key_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(store.data_path.stat().st_mode), 0o600)


class TestMediaMTXClient(unittest.TestCase):
    def test_add_falls_back_to_patch_with_tcp_on_demand_source(self):
        class Client(MediaMTXClient):
            def __init__(self):
                super().__init__("http://gateway:9997")
                self.calls = []

            def _request(self, method, endpoint, payload=None):
                self.calls.append((method, endpoint, payload))
                return 409 if method == "POST" else 204

        client = Client()
        client.sync("dock-1", "rtsp://admin:secret@192.168.1.7/live")
        self.assertEqual([call[:2] for call in client.calls], [
            ("POST", "add/dock-1"), ("PATCH", "patch/dock-1"),
        ])
        self.assertEqual(client.calls[-1][2], {
            "source": "rtsp://admin:secret@192.168.1.7/live",
            "sourceOnDemand": True,
            "rtspTransport": "tcp",
        })

    def test_delete_is_idempotent(self):
        class Client(MediaMTXClient):
            def _request(self, *_args, **_kwargs):
                return 404

        Client("http://gateway:9997").delete("dock-1")

    def test_ready_paths_sanitizes_gateway_payload(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"items":[{"name":"cam_005","ready":true,"source":{"url":"rtsp://secret"},"tracks2":[{"codec":"H264","codecProps":{"width":1280,"height":720}}]},{"name":"offline","ready":false}]}'

        with patch("src.cameras.mediamtx.urlopen", return_value=Response()):
            paths = MediaMTXClient("http://gateway:9997").ready_paths()

        self.assertEqual(paths, [{
            "name": "cam_005",
            "tracks": [{"codec": "H264", "width": 1280, "height": 720}],
        }])
        self.assertNotIn("secret", str(paths))


class _Socket:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def _resolver(ip):
    return lambda host, port, type: [(None, None, None, None, (ip, port))]


class TestProbe(unittest.TestCase):
    def test_denies_public_and_loopback_targets(self):
        for ip in ("8.8.8.8", "127.0.0.1", "169.254.1.1"):
            result = RtspProbe(resolver=_resolver(ip), connector=lambda *_args, **_kwargs: _Socket()).run("rtsp://camera/live")
            self.assertFalse(result["ok"])
            self.assertEqual(result["code"], "ssrf_denied")

    def test_returns_bounded_structured_connection_diagnostics(self):
        result = RtspProbe(resolver=_resolver("192.168.12.5"), connector=lambda *_args, **_kwargs: _Socket()).run("rtsp://camera/live")
        self.assertEqual(result["code"], "connected")
        self.assertTrue(result["ok"])
        self.assertEqual(result["addresses"], ["192.168.12.5"])
        self.assertIn("latency_ms", result)

    def test_uses_a_prevalidated_address_without_resolving_again(self):
        connected = []
        probe = RtspProbe(
            resolver=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("resolved")),
            connector=lambda target, **_kwargs: connected.append(target) or _Socket(),
        )

        result = probe.run(
            "rtsp://camera/live",
            validated_addresses=["192.168.12.5"],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(connected, [("192.168.12.5", 554)])

    def test_rejects_mixed_dns_answers_and_pins_frame_probe(self):
        calls = []

        def resolver(host, port, type):
            calls.append((host, port))
            return [
                (None, None, None, None, ("192.168.12.5", port)),
                (None, None, None, None, ("8.8.8.8", port)),
            ]

        probe = RtspProbe(resolver=resolver, connector=lambda *_args, **_kwargs: _Socket())
        self.assertEqual(probe.run("rtsp://camera/live")["code"], "ssrf_denied")
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            RtspProbe.pin_url("rtsp://user:pass@camera:8554/live", "192.168.12.5"),
            "rtsp://user:pass@192.168.12.5:8554/live",
        )

    def test_pin_url_does_not_double_encode_credentials(self):
        credentialed = RtspProbe.with_credentials(
            "rtsp://camera/live", "user@example.com", "p@ss:word"
        )
        self.assertEqual(
            RtspProbe.pin_url(credentialed, "192.168.12.5"),
            "rtsp://user%40example.com:p%40ss%3Aword@192.168.12.5/live",
        )

    def test_allows_only_rfc1918_ula_and_explicit_lab_loopback(self):
        probe = RtspProbe()
        for address in ("10.1.2.3", "172.16.10.2", "192.168.20.3", "fd00::20"):
            self.assertTrue(probe._is_allowed(address))
        for address in ("127.0.0.1", "169.254.1.1", "192.0.2.1", "198.18.0.1"):
            self.assertFalse(probe._is_allowed(address))
        self.assertTrue(RtspProbe(allow_loopback=True)._is_allowed("127.0.0.1"))


class _Probe:
    def run(self, _url, **_kwargs):
        return {"ok": True, "code": "connected", "message": "fake", "latency_ms": 0}

    def allowed_host(self, _host, _port):
        return ["192.168.1.7"]


class TestCameraApi(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(
            camera_directory=Path(self.tmp.name),
            rtsp_probe=_Probe(),
            onvif=OnvifAdapter(),
        ))
        self.headers = {"X-API-Key": KEY}

    def tearDown(self):
        self.tmp.cleanup()

    def _request(self, method, path, **kwargs):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
            return self.client.request(method, path, headers=self.headers, **kwargs)

    def _body(self):
        return {"id": "dock-1", "name": "Loading Dock", "rtsp_url": "rtsp://192.168.1.25/live", "username": "admin", "password": "secret-password", "zone": "dock", "profile": "warehouse"}

    def test_auth_validation_and_non_disclosure(self):
        with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
            self.assertEqual(self.client.post("/api/cameras", json=self._body()).status_code, 401)
        invalid = self._body() | {"id": "bad id"}
        self.assertEqual(self._request("POST", "/api/cameras", json=invalid).status_code, 422)
        response = self._request("POST", "/api/cameras?expected_revision=0", json=self._body())
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("secret-password", response.text)
        listed = self._request("GET", "/api/cameras")
        camera = next(item for item in listed.json()["cameras"] if item["id"] == "dock-1")
        self.assertEqual(camera["zone"], "dock")
        self.assertTrue(camera["credential_configured"])
        self.assertNotIn("credential_ref", camera)
        self.assertNotIn("secret-password", listed.text)
        self.assertNotIn("secret-password", (Path(self.tmp.name) / "cameras.json").read_text())

    def test_conflict_diagnostics_readiness_and_optional_onvif(self):
        self.assertEqual(self._request("POST", "/api/cameras?expected_revision=0", json=self._body()).status_code, 201)
        update = self._body() | {"name": "Changed"}
        self.assertEqual(self._request("PUT", "/api/cameras/dock-1?expected_revision=0", json=update).status_code, 409)
        self.assertEqual(self._request("POST", "/api/cameras/dock-1/test").json()["code"], "connected")
        self.assertTrue(self._request("GET", "/api/cameras/readiness").json()["ready"])
        self.assertEqual(self._request("GET", "/api/onvif/discover").json()["available"], False)
        self.assertEqual(self._request("GET", "/api/onvif/profiles?host=cam.local").status_code, 422)

    def test_revisionless_enable_and_zone_writes_reject_concurrent_edits(self):
        created = self._request(
            "POST",
            "/api/cameras",
            json=self._body() | {"enabled": False},
        )
        self.assertEqual(created.status_code, 201)
        repo = self.client.app.state.camera_repository
        original_save = repo.save
        raced = False

        def race_enable(camera, expected_revision):
            nonlocal raced
            if not raced:
                raced = True
                revision, cameras = repo.list()
                original_save(
                    cameras[0].model_copy(update={"name": "Concurrent name"}),
                    revision,
                )
            return original_save(camera, expected_revision)

        with patch.object(repo, "save", side_effect=race_enable):
            enabled = self._request("POST", "/api/cameras/dock-1/enable")
        self.assertEqual(enabled.status_code, 409)
        current = repo.get("dock-1")
        self.assertEqual(current.name, "Concurrent name")
        self.assertFalse(current.enabled)

        raced = False

        def race_zones(camera, expected_revision):
            nonlocal raced
            if not raced:
                raced = True
                revision, cameras = repo.list()
                original_save(
                    cameras[0].model_copy(update={"location": "Concurrent location"}),
                    revision,
                )
            return original_save(camera, expected_revision)

        with patch.object(repo, "save", side_effect=race_zones):
            zones = self._request(
                "PUT",
                "/api/cameras/dock-1/zones",
                json={"zones": []},
            )
        self.assertEqual(zones.status_code, 409)
        current = repo.get("dock-1")
        self.assertEqual(current.location, "Concurrent location")
        self.assertEqual(current.zones, [])

    def test_onvif_profile_connection_uses_validated_address(self):
        class RecordingOnvif:
            def __init__(self):
                self.hosts = []

            def discover(self):
                return {
                    "available": True,
                    "devices": [{"name": "Rebind camera", "host": "rebind.example", "onvif_port": 80}],
                }

            def profiles(self, host, *_args):
                self.hosts.append(host)
                return {"available": True, "profiles": []}

        adapter = RecordingOnvif()
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp),
                rtsp_probe=_Probe(),
                onvif=adapter,
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                discovered = client.post(
                    "/api/cameras/discover",
                    headers=self.headers,
                    json={"protocol": "onvif"},
                )
                response = client.post(
                    "/api/onvif/profiles",
                    headers=self.headers,
                    json={"discovery_id": discovered.json()["devices"][0]["discovery_id"]},
                )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(adapter.hosts, ["192.168.1.7"])

    def test_onvif_profile_lookup_rejects_targets_not_returned_by_discovery(self):
        class RecordingOnvif:
            def __init__(self):
                self.hosts = []

            def discover(self):
                return {"available": True, "devices": []}

            def profiles(self, host, *_args):
                self.hosts.append(host)
                return {"available": True, "profiles": []}

        adapter = RecordingOnvif()
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=_Probe(), onvif=adapter))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                response = client.post(
                    "/api/onvif/profiles",
                    headers=self.headers,
                    json={"discovery_id": "a" * 32, "username": "installer", "password": "secret"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(adapter.hosts, [])

    def test_onvif_adapter_degrades_on_device_errors(self):
        class Failing:
            @staticmethod
            def discover():
                raise RuntimeError("offline")
        self.assertFalse(OnvifAdapter(Failing).discover()["available"])

    def test_gateway_discovery_returns_unregistered_ready_streams(self):
        class Gateway:
            @staticmethod
            def ready_paths():
                return [
                    {"name": "cam_005", "tracks": [{"codec": "H264", "width": 1280, "height": 720}]},
                    {"name": "dock-1", "tracks": [{"codec": "H264", "width": 640, "height": 360}]},
                ]

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp),
                rtsp_probe=_Probe(),
                mediamtx=Gateway(),
            ))
            with patch.dict(os.environ, {
                "KSI_CCTV_API_KEY": KEY,
                "KSI_CCTV_GATEWAY_RTSP_URL": "rtsp://gateway.internal:8554",
            }):
                self.assertEqual(client.post("/api/cameras", headers=self.headers, json=self._body()).status_code, 201)
                response = client.post(
                    "/api/cameras/discover",
                    headers=self.headers,
                    json={"protocol": "gateway"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["devices"]), 1)
        self.assertEqual(response.json()["devices"][0]["rtsp_url"], "rtsp://gateway.internal:8554/cam_005")
        self.assertNotIn("dock-1", response.text)

    def test_gateway_discovery_defaults_to_the_appliance_gateway_service(self):
        class Gateway:
            @staticmethod
            def ready_paths():
                return [{"name": "cam_005", "tracks": [{"codec": "H264"}]}]

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=_Probe(), mediamtx=Gateway()))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}, clear=True):
                response = client.post(
                    "/api/cameras/discover",
                    headers=self.headers,
                    json={"protocol": "gateway"},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["devices"][0]["rtsp_url"], "rtsp://mediamtx:8554/cam_005")

    def test_unified_discovery_combines_onvif_and_gateway_sources(self):
        class Onvif:
            @staticmethod
            def discover():
                return {
                    "available": True,
                    "devices": [{"name": "Front Door", "host": "192.168.1.20", "onvif_port": 8080}],
                }

        class Gateway:
            @staticmethod
            def ready_paths():
                return [{"name": "nvr-1", "tracks": [{"codec": "H264", "width": 1280, "height": 720}]}]

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp),
                rtsp_probe=_Probe(),
                onvif=Onvif(),
                mediamtx=Gateway(),
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                response = client.post("/api/cameras/discover", headers=self.headers, json={"protocol": "all"})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["available"])
        self.assertEqual(
            [(device["name"], device["source_type"]) for device in response.json()["devices"]],
            [("Front Door", "onvif"), ("연결된 영상 nvr-1", "gateway")],
        )
        self.assertTrue(response.json()["devices"][0]["discovery_id"])
        self.assertEqual(response.json()["sources"]["onvif"], {"available": True, "reason": None})

    def test_transient_probe_and_generated_camera_id(self):
        probe = self._request("POST", "/api/cameras/test", json={
            "rtsp_url": "rtsp://192.168.1.40/live",
            "username": "viewer",
            "password": "secret",
            "verify_stream": True,
        })
        self.assertEqual(probe.status_code, 200)
        created = self._request("POST", "/api/cameras", json={
            "name": "정문",
            "rtsp_url": "rtsp://192.168.1.40/live",
            "profile": "general",
            "enabled_events": ["fire", "smoke"],
            "zones": [{
                "id": "restricted",
                "type": "polygon",
                "points": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.5, "y": 0.9}],
            }],
        })
        self.assertEqual(created.status_code, 201, created.text)
        self.assertRegex(created.json()["camera"]["id"], r"^cam_[0-9a-f]{8}$")

    def test_enabled_camera_is_not_saved_when_server_first_frame_check_fails(self):
        class FailingProbe(_Probe):
            def run(self, _url, **_kwargs):
                return {"ok": False, "code": "no_frame", "message": "no frame"}

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=FailingProbe()))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                response = client.post("/api/cameras", headers=self.headers, json=self._body())

            self.assertEqual(response.status_code, 400)
            self.assertEqual(client.app.state.camera_repository.list()[1], [])

    def test_draft_probe_reuses_credentials_only_for_the_same_target(self):
        class RecordingProbe(_Probe):
            def __init__(self):
                self.calls = []

            def run(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return super().run(url, **kwargs)

        probe = RecordingProbe()
        self.client.app.state.rtsp_probe = probe
        created = self._request(
            "POST",
            "/api/cameras",
            json=self._body() | {"enabled": False},
        )
        self.assertEqual(created.status_code, 201)

        same_target = self._request(
            "POST",
            "/api/cameras/dock-1/test-draft",
            json={"rtsp_url": "rtsp://192.168.1.25/alternate"},
        )
        self.assertEqual(same_target.status_code, 200)
        self.assertEqual(probe.calls[-1][1]["username"], "admin")
        self.assertEqual(probe.calls[-1][1]["password"], "secret-password")

        other_target = self._request(
            "POST",
            "/api/cameras/dock-1/test-draft",
            json={"rtsp_url": "rtsp://192.168.1.99/live"},
        )
        self.assertEqual(other_target.status_code, 400)
        self.assertNotIn("secret-password", other_target.text)

        replacement = self._request(
            "POST",
            "/api/cameras/dock-1/test-draft",
            json={
                "rtsp_url": "rtsp://192.168.1.99/live",
                "username": "new-user",
                "password": "new-password",
            },
        )
        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(probe.calls[-1][1]["username"], "new-user")
        self.assertEqual(probe.calls[-1][1]["password"], "new-password")

        without_auth = self._request(
            "POST",
            "/api/cameras/dock-1/test-draft",
            json={"rtsp_url": "rtsp://192.168.1.99/live", "clear_credentials": True},
        )
        self.assertEqual(without_auth.status_code, 200)
        self.assertIsNone(probe.calls[-1][1]["username"])
        self.assertIsNone(probe.calls[-1][1]["password"])

    def test_dns_identity_change_withholds_stored_credentials_until_reentered(self):
        class MutableProbe(_Probe):
            def __init__(self):
                self.address = "192.168.1.25"
                self.calls = []

            def allowed_host(self, _host, _port):
                return [self.address]

            def run(self, url, **kwargs):
                self.calls.append((url, kwargs))
                return super().run(url, **kwargs)

        probe = MutableProbe()
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=probe))
            body = self._body() | {
                "rtsp_url": "rtsp://camera.local/live",
                "enabled": False,
            }
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                created = client.post("/api/cameras", headers=self.headers, json=body)
                probe.address = "192.168.1.99"
                saved_test = client.post("/api/cameras/dock-1/test", headers=self.headers)
                draft = client.post(
                    "/api/cameras/dock-1/test-draft",
                    headers=self.headers,
                    json={"rtsp_url": "rtsp://camera.local/live"},
                )
                replacement = client.post(
                    "/api/cameras/dock-1/test-draft",
                    headers=self.headers,
                    json={
                        "rtsp_url": "rtsp://camera.local/live",
                        "username": "new-user",
                        "password": "new-password",
                    },
                )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(saved_test.status_code, 409)
        self.assertEqual(draft.status_code, 400)
        self.assertNotIn("secret-password", draft.text)
        self.assertEqual(replacement.status_code, 200)
        self.assertEqual(probe.calls[-1][1]["username"], "new-user")
        self.assertEqual(probe.calls[-1][1]["password"], "new-password")
        self.assertEqual(probe.calls[-1][1]["validated_addresses"], ["192.168.1.99"])

    def test_legacy_unscoped_credentials_fail_closed_until_reentered(self):
        repo = self.client.app.state.camera_repository
        repo.save(
            CameraConfig(
                id="legacy",
                name="Legacy",
                rtsp_url="rtsp://camera.local/live",
                credential_ref="camera:legacy",
            ),
            expected_revision=0,
        )
        store = self.client.app.state.camera_credentials
        store.put("camera:legacy", "legacy-user", "legacy-password")

        saved_test = self._request("POST", "/api/cameras/legacy/test")
        draft = self._request(
            "POST",
            "/api/cameras/legacy/test-draft",
            json={"rtsp_url": "rtsp://camera.local/live"},
        )
        apply = self._request("POST", "/api/cameras/legacy/apply")

        self.assertEqual(saved_test.status_code, 409)
        self.assertEqual(draft.status_code, 400)
        self.assertEqual(apply.status_code, 503)
        self.assertNotIn("legacy-password", saved_test.text + draft.text + apply.text)

        migrated = self._request(
            "PUT",
            "/api/cameras/legacy?expected_revision=1",
            json={
                "name": "Legacy",
                "rtsp_url": "rtsp://camera.local/live",
                "enabled": False,
                "profile": "general",
                "enabled_events": [],
                "username": "legacy-user",
                "password": "legacy-password",
            },
        )
        self.assertEqual(migrated.status_code, 200, migrated.text)
        self.assertEqual(store.get("camera:legacy")["target_scope"], "554|192.168.1.7")
        self.assertEqual(self._request("POST", "/api/cameras/legacy/test").status_code, 200)

    def test_update_requires_explicit_credentials_for_a_new_target_and_can_remove_them(self):
        created = self._request(
            "POST",
            "/api/cameras",
            json=self._body() | {"enabled": False},
        )
        self.assertEqual(created.status_code, 201)
        revision = created.json()["revision"]
        update = self._body() | {
            "enabled": False,
            "rtsp_url": "rtsp://192.168.1.99/live",
        }
        update.pop("username")
        update.pop("password")

        rejected = self._request(
            "PUT",
            f"/api/cameras/dock-1?expected_revision={revision}",
            json=update,
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertNotIn("secret-password", rejected.text)

        removed = self._request(
            "PUT",
            f"/api/cameras/dock-1?expected_revision={revision}",
            json=update | {"clear_credentials": True},
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertFalse(removed.json()["camera"]["credential_configured"])
        self.assertIsNone(self.client.app.state.camera_credentials.get("camera:dock-1"))

    def test_runtime_apply_enable_disable_and_delete(self):
        class Pipeline:
            def __init__(self):
                self.applied = []
                self.removed = []

            def apply_camera(self, camera_id, stream_url, **kwargs):
                self.applied.append((camera_id, stream_url, kwargs))
                return {"applied": True}

            def remove_camera(self, camera_id):
                self.removed.append(camera_id)
                return True

            def get_status(self):
                return {"cameras": {}}

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline()
            client = TestClient(create_app(
                camera_directory=Path(tmp),
                rtsp_probe=_Probe(),
                onvif=OnvifAdapter(),
                pipeline=pipeline,
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                created = client.post("/api/cameras", headers=self.headers, json=self._body())
                self.assertTrue(created.json()["applied"])
                self.assertIn("admin:secret-password@", pipeline.applied[0][1])
                disabled = client.post("/api/cameras/dock-1/disable", headers=self.headers)
                self.assertEqual(disabled.status_code, 200)
                self.assertFalse(disabled.json()["enabled"])
                enabled = client.post("/api/cameras/dock-1/enable", headers=self.headers)
                self.assertEqual(enabled.status_code, 200)
                self.assertTrue(enabled.json()["enabled"])
                deleted = client.delete("/api/cameras/dock-1", headers=self.headers)
                self.assertEqual(deleted.status_code, 200)
            self.assertGreaterEqual(len(pipeline.applied), 2)
            self.assertIn("dock-1", pipeline.removed)

    def test_failed_connection_update_restores_last_known_good_configuration(self):
        class Pipeline:
            def __init__(self):
                self.applied = []

            def apply_camera(self, camera_id, stream_url, **_kwargs):
                self.applied.append((camera_id, stream_url))
                if "bad-stream" in stream_url:
                    raise RuntimeError("cannot open stream")

            @staticmethod
            def remove_camera(_camera_id):
                return True

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline()
            client = TestClient(create_app(
                camera_directory=Path(tmp),
                rtsp_probe=_Probe(),
                pipeline=pipeline,
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                created = client.post("/api/cameras", headers=self.headers, json=self._body())
                revision = created.json()["revision"]
                update = self._body() | {
                    "name": "Broken update",
                    "rtsp_url": "rtsp://192.168.1.99/bad-stream",
                    "clear_credentials": True,
                }
                update.pop("username")
                update.pop("password")
                failed = client.put(
                    f"/api/cameras/dock-1?expected_revision={revision}",
                    headers=self.headers,
                    json=update,
                )
                current = client.get("/api/cameras/dock-1", headers=self.headers)
                restored_credential = client.app.state.camera_credentials.get("camera:dock-1")

        self.assertEqual(failed.status_code, 503)
        self.assertIn("이전 설정으로 복구", failed.json()["detail"])
        self.assertEqual(current.json()["camera"]["name"], "Loading Dock")
        self.assertEqual(current.json()["camera"]["rtsp_url"], "rtsp://192.168.1.25/live")
        self.assertEqual(restored_credential["password"], "secret-password")
        self.assertEqual(pipeline.applied[-1][1], "rtsp://admin:secret-password@192.168.1.7/live")

    def test_gateway_create_disable_delete_and_errors_do_not_disclose_credentials(self):
        class Pipeline:
            def apply_camera(self, *_args, **_kwargs):
                pass

            def remove_camera(self, _camera_id):
                return True

        class Gateway:
            def __init__(self):
                self.sources = []
                self.deleted = []

            def sync(self, camera_id, source):
                self.sources.append((camera_id, source))
                raise RuntimeError(source)

            def delete(self, camera_id):
                self.deleted.append(camera_id)

        with tempfile.TemporaryDirectory() as tmp:
            gateway = Gateway()
            client = TestClient(create_app(
                camera_directory=Path(tmp), rtsp_probe=_Probe(), pipeline=Pipeline(), mediamtx=gateway,
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                created = client.post("/api/cameras", headers=self.headers, json=self._body())
                self.assertEqual(created.status_code, 201)
                self.assertFalse(created.json()["applied"])
                self.assertFalse(created.json()["gateway_synced"])
                self.assertEqual(created.json()["apply_error"], "gateway:RuntimeError")
                self.assertNotIn("secret-password", created.text)
                disabled = client.post("/api/cameras/dock-1/disable", headers=self.headers)
                self.assertEqual(disabled.status_code, 200)
                deleted = client.delete("/api/cameras/dock-1", headers=self.headers)
                self.assertEqual(deleted.status_code, 200)
            self.assertIn("admin:secret-password@192.168.1.7", gateway.sources[0][1])
            self.assertEqual(gateway.deleted, ["dock-1", "dock-1"])

    def test_delete_keeps_record_and_credentials_when_gateway_cleanup_fails(self):
        class Gateway:
            def sync(self, *_args):
                return None

            def delete(self, _camera_id):
                raise RuntimeError("source password must not escape")

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp),
                rtsp_probe=_Probe(),
                mediamtx=Gateway(),
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                created = client.post("/api/cameras", headers=self.headers, json=self._body())
                deleted = client.delete("/api/cameras/dock-1", headers=self.headers)
                listed = client.get("/api/cameras/dock-1", headers=self.headers)

            self.assertEqual(created.status_code, 201)
            self.assertEqual(deleted.status_code, 503)
            self.assertNotIn("source password", deleted.text)
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(
                client.app.state.camera_credentials.get("camera:dock-1")["password"],
                "secret-password",
            )

    def test_update_uses_path_credential_ref_and_preserves_omitted_credentials(self):
        first = self._request("POST", "/api/cameras", json=self._body() | {"id": "one", "enabled": False})
        second = self._request("POST", "/api/cameras", json=self._body() | {
            "id": "two", "username": "second", "password": "second-secret", "enabled": False,
        })
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 201)
        update = self._body() | {"name": "One updated"}
        update.pop("id")
        update.pop("username")
        update.pop("password")
        update["enabled"] = False
        self.assertEqual(self._request("PUT", "/api/cameras/one", json=update).status_code, 200)
        store = self.client.app.state.camera_credentials
        self.assertEqual(store.get("camera:one")["password"], "secret-password")
        self.assertEqual(store.get("camera:two")["password"], "second-secret")
        self.assertIsNone(store.get("camera:None"))

    def test_rejected_duplicate_does_not_delete_existing_credentials(self):
        self.assertEqual(self._request("POST", "/api/cameras", json=self._body() | {"enabled": False}).status_code, 201)
        rejected = self._request("POST", "/api/cameras", json=self._body() | {"password": "replacement"})
        self.assertEqual(rejected.status_code, 409)
        store = self.client.app.state.camera_credentials
        self.assertEqual(store.get("camera:dock-1")["password"], "secret-password")

    def test_private_target_validation_and_camera_limit(self):
        class DeniedProbe(_Probe):
            def allowed_host(self, _host, _port):
                return []

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=DeniedProbe()))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                denied = client.post("/api/cameras", headers=self.headers, json=self._body())
            self.assertEqual(denied.status_code, 422)

        self.assertEqual(self._request("POST", "/api/cameras", json=self._body()).status_code, 201)
        self.client.app.state.rtsp_probe = DeniedProbe()
        changed = self._body() | {"name": "Denied update"}
        self.assertEqual(self._request("PUT", "/api/cameras/dock-1", json=changed).status_code, 422)
        self.assertEqual(self._request("POST", "/api/cameras/dock-1/apply").status_code, 422)
        self.client.app.state.rtsp_probe = _Probe()

        legacy_ids = {
            item["id"] for item in self.client.app.state.config.load_cameras() if item.get("id")
        }
        remaining = CameraRepository.MAX_CAMERAS - len(legacy_ids | {"dock-1"})
        for index in range(remaining):
            body = self._body() | {"id": f"limit-{index}", "username": None, "password": None}
            self.assertEqual(self._request("POST", "/api/cameras", json=body).status_code, 201)
        over = self._request("POST", "/api/cameras", json=self._body() | {"id": "limit-over"})
        self.assertEqual(over.status_code, 409)
        self.assertIn("limit", over.json()["detail"])

    def test_disabled_apply_profile_and_zones_do_not_mount(self):
        class Pipeline:
            def __init__(self):
                self.applied = []
                self.removed = []

            def apply_camera(self, *args, **kwargs):
                self.applied.append((args, kwargs))

            def remove_camera(self, camera_id):
                self.removed.append(camera_id)
                return False

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline()
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=_Probe(), pipeline=pipeline))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                created = client.post("/api/cameras", headers=self.headers, json=self._body() | {"enabled": False})
                self.assertEqual(created.status_code, 201)
                self.assertEqual(client.post("/api/cameras/dock-1/apply", headers=self.headers).status_code, 200)
                zones = client.put("/api/cameras/dock-1/zones", headers=self.headers, json={"zones": []})
                self.assertEqual(zones.status_code, 200)
                profile = client.put("/api/cameras/dock-1/profile", headers=self.headers, json={
                    "domain": "general", "enabled_events": []
                })
                self.assertEqual(profile.status_code, 200)
            self.assertEqual(pipeline.applied, [])

    def test_enabled_zone_update_reapplies_with_resolution(self):
        class Pipeline:
            def __init__(self):
                self.applied = []

            def apply_camera(self, *args, **kwargs):
                self.applied.append((args, kwargs))

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline()
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=_Probe(), pipeline=pipeline))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                body = self._body() | {"resolution": [640, 360]}
                self.assertEqual(client.post("/api/cameras", headers=self.headers, json=body).status_code, 201)
                response = client.put("/api/cameras/dock-1/zones", headers=self.headers, json={
                    "zones": [{
                        "id": "gate",
                        "type": "line",
                        "direction": "negative",
                        "points": [{"x": 0.1, "y": 0.5}, {"x": 0.9, "y": 0.5}],
                    }]
                })
            self.assertTrue(response.json()["applied"])
            self.assertEqual(len(pipeline.applied), 2)
            self.assertEqual(pipeline.applied[-1][1]["resize"], (640, 360))
            runtime_zone = pipeline.applied[-1][1]["profile"]["zones"]["gate"]
            self.assertEqual(runtime_zone["type"], "line")
            self.assertEqual(runtime_zone["direction"], "negative")

    def test_tracking_snapshot_requires_managed_camera_and_pipeline(self):
        class Pipeline:
            def apply_camera(self, *_args, **_kwargs):
                return None

            def remove_camera(self, _camera_id):
                return True

            def get_tracking_snapshot(self, camera_id):
                return {
                    "camera_id": camera_id,
                    "stale": False,
                    "tracks": [{"track_id": 4}],
                    "zones": [],
                }

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp), rtsp_probe=_Probe(), pipeline=Pipeline()
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                unauthorized = client.get("/api/cameras/dock-1/tracking")
                missing = client.get(
                    "/api/cameras/missing/tracking", headers=self.headers
                )
                created = client.post(
                    "/api/cameras", headers=self.headers, json=self._body()
                )
                tracking = client.get(
                    "/api/cameras/dock-1/tracking", headers=self.headers
                )
                disabled = client.post(
                    "/api/cameras/dock-1/disable", headers=self.headers
                )
                disabled_tracking = client.get(
                    "/api/cameras/dock-1/tracking", headers=self.headers
                )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(created.status_code, 201)
        self.assertEqual(tracking.status_code, 200)
        self.assertEqual(tracking.json()["tracks"][0]["track_id"], 4)
        self.assertEqual(disabled.status_code, 200)
        self.assertEqual(disabled_tracking.status_code, 409)

    def test_profile_update_excludes_globally_disabled_events(self):
        self.assertEqual(
            self._request("POST", "/api/cameras", json=self._body() | {"enabled": False}).status_code,
            201,
        )

        # 영역 기반 이벤트는 필요한 영역이 없으면 활성화할 수 없다.
        missing_zone = self._request(
            "PUT",
            "/api/cameras/dock-1/profile",
            json={"domain": "general", "enabled_events": ["fire", "restricted_area"]},
        )
        self.assertEqual(missing_zone.status_code, 400)
        self.assertIn("영역 설정이 필요한", missing_zone.json()["detail"])

        zones = self._request(
            "PUT",
            "/api/cameras/dock-1/zones",
            json={"zones": [{
                "id": "restricted",
                "type": "polygon",
                "points": [{"x": 0.1, "y": 0.1}, {"x": 0.9, "y": 0.1}, {"x": 0.5, "y": 0.9}],
            }]},
        )
        self.assertEqual(zones.status_code, 200)
        response = self._request(
            "PUT",
            "/api/cameras/dock-1/profile",
            json={"domain": "general", "enabled_events": ["fire", "restricted_area"]},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["enabled_events"],
            ["fire", "restricted_area"],
        )

        removal = self._request(
            "PUT",
            "/api/cameras/dock-1/zones",
            json={"zones": []},
        )
        self.assertEqual(removal.status_code, 400)
        self.assertIn("필요한 영역을 삭제", removal.json()["detail"])

        # 미정의 이벤트는 거부(침묵 드롭 회귀).
        bad = self._request(
            "PUT",
            "/api/cameras/dock-1/profile",
            json={"domain": "general", "enabled_events": ["not_an_event"]},
        )
        self.assertEqual(bad.status_code, 400)

        health = self._request(
            "PUT",
            "/api/cameras/dock-1/profile",
            json={"domain": "general", "enabled_events": ["camera_disconnect"]},
        )
        self.assertEqual(health.status_code, 200)

        planned = self._request(
            "PUT",
            "/api/cameras/dock-1/profile",
            json={"domain": "general", "enabled_events": ["wrong_way"]},
        )
        self.assertEqual(planned.status_code, 400)
        self.assertIn("아직 사용할 수 없는", planned.json()["detail"])

        experimental = self._request(
            "PUT",
            "/api/cameras/dock-1/profile",
            json={"domain": "general", "enabled_events": ["fall"]},
        )
        self.assertEqual(experimental.status_code, 400)
        self.assertIn("사용할 수 없는", experimental.json()["detail"])

    def test_create_rejects_unknown_event_selection(self):
        response = self._request(
            "POST",
            "/api/cameras",
            json=self._body() | {"profile": "general", "enabled_events": ["made_up"]},
        )
        self.assertEqual(response.status_code, 400)

    def test_readiness_uses_mounted_camera_frame_health(self):
        class Pipeline:
            def __init__(self):
                self.camera_status = {"connected": True, "last_frame_time": 1.0, "is_stale": False}

            def apply_camera(self, *_args, **_kwargs):
                return {"applied": True}

            def get_status(self):
                return {"cameras": {"dock-1": self.camera_status}}

        with tempfile.TemporaryDirectory() as tmp:
            pipeline = Pipeline()
            client = TestClient(create_app(camera_directory=Path(tmp), rtsp_probe=_Probe(), pipeline=pipeline))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                self.assertEqual(client.post("/api/cameras", headers=self.headers, json=self._body()).status_code, 201)
                self.assertTrue(client.get("/api/cameras/readiness", headers=self.headers).json()["ready"])
                pipeline.camera_status["is_stale"] = True
                self.assertFalse(client.get("/api/cameras/readiness", headers=self.headers).json()["ready"])

    def test_readiness_prefers_pipeline_health_state(self):
        class Pipeline:
            def apply_camera(self, *_args, **_kwargs):
                return {"applied": True}

            def get_status(self):
                return {"cameras": {"dock-1": {
                    "connected": True,
                    "last_frame_time": 1.0,
                    "is_stale": False,
                    "health_state": "stale",
                }}}

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp), rtsp_probe=_Probe(), pipeline=Pipeline()
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                self.assertEqual(
                    client.post("/api/cameras", headers=self.headers, json=self._body()).status_code,
                    201,
                )
                response = client.get("/api/cameras/readiness", headers=self.headers)
            self.assertFalse(response.json()["ready"])
            self.assertEqual(response.json()["healthy_cameras"], [])

    def test_snapshot_denies_missing_and_disabled_managed_cameras(self):
        class Pipeline:
            def apply_camera(self, *_args, **_kwargs):
                return {"applied": True}

            def remove_camera(self, _camera_id):
                return True

            def get_camera_snapshot(self, _camera_id):
                return b"stale-frame"

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp),
                rtsp_probe=_Probe(),
                pipeline=Pipeline(),
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                missing = client.get("/api/cameras/missing/snapshot", headers=self.headers)
                client.post("/api/cameras", headers=self.headers, json=self._body())
                client.post("/api/cameras/dock-1/disable", headers=self.headers)
                disabled = client.get("/api/cameras/dock-1/snapshot", headers=self.headers)

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(disabled.status_code, 409)

    def test_snapshot_requires_auth_and_disables_caching(self):
        class Pipeline:
            def apply_camera(self, *_args, **_kwargs):
                return {"applied": True}

            def get_camera_snapshot(self, _camera_id):
                return b"jpeg-frame"

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(
                camera_directory=Path(tmp), rtsp_probe=_Probe(), pipeline=Pipeline()
            ))
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": KEY}):
                client.post("/api/cameras", headers=self.headers, json=self._body())
                missing_key = client.get("/api/cameras/dock-1/snapshot")
                wrong_key = client.get(
                    "/api/cameras/dock-1/snapshot",
                    headers={"X-API-Key": "wrong"},
                )
                response = client.get(
                    "/api/cameras/dock-1/snapshot", headers=self.headers
                )

        self.assertEqual(missing_key.status_code, 401)
        self.assertEqual(wrong_key.status_code, 401)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")


class TestWsDiscovery(unittest.TestCase):
    def test_parses_onvif_probe_match(self):
        payload = b"""<?xml version='1.0'?>
        <Envelope xmlns='http://www.w3.org/2003/05/soap-envelope'
          xmlns:a='http://schemas.xmlsoap.org/ws/2004/08/addressing'
          xmlns:d='http://schemas.xmlsoap.org/ws/2005/04/discovery'>
          <Body><d:ProbeMatches><d:ProbeMatch>
          <a:EndpointReference><a:Address>urn:uuid:camera-1</a:Address></a:EndpointReference>
          <d:XAddrs>http://192.168.10.20/onvif/device_service</d:XAddrs>
          <d:Scopes>onvif://www.onvif.org/name/Front%20Door onvif://www.onvif.org/hardware/TestCam</d:Scopes>
          </d:ProbeMatch></d:ProbeMatches></Body></Envelope>"""
        parsed = WsDiscoveryClient._parse(payload, "192.168.10.20")
        self.assertEqual(parsed["endpoint"], "urn:uuid:camera-1")
        self.assertEqual(parsed["name"], "Front Door")
        self.assertEqual(parsed["model"], "TestCam")
        self.assertEqual(parsed["onvif_url"], "http://192.168.10.20/onvif/device_service")
        self.assertEqual(parsed["onvif_port"], 80)

    def test_preserves_non_default_onvif_service_port(self):
        payload = b"""<?xml version='1.0'?>
        <Envelope xmlns='http://www.w3.org/2003/05/soap-envelope'
          xmlns:a='http://schemas.xmlsoap.org/ws/2004/08/addressing'
          xmlns:d='http://schemas.xmlsoap.org/ws/2005/04/discovery'>
          <Body><d:ProbeMatches><d:ProbeMatch>
          <a:EndpointReference><a:Address>urn:uuid:camera-2</a:Address></a:EndpointReference>
          <d:XAddrs>http://192.168.10.21:8080/onvif/device_service</d:XAddrs>
          </d:ProbeMatch></d:ProbeMatches></Body></Envelope>"""

        parsed = WsDiscoveryClient._parse(payload, "192.168.10.21")

        self.assertEqual(parsed["onvif_port"], 8080)

    def test_credentials_are_url_encoded(self):
        url = RtspProbe.with_credentials("rtsp://192.168.1.2/live", "user@site", "p:a/ss")
        self.assertEqual(url, "rtsp://user%40site:p%3Aa%2Fss@192.168.1.2/live")

    def test_optional_zeep_adapter_returns_sanitized_media_profiles(self):
        encoder = SimpleNamespace(
            Encoding="H264",
            Resolution=SimpleNamespace(Width=1280, Height=720),
            RateControl=SimpleNamespace(FrameRateLimit=15),
        )
        profile = SimpleNamespace(token="sub", Name="Sub Stream", VideoEncoderConfiguration=encoder)

        class Media:
            def GetProfiles(self):
                return [profile]

            def GetStreamUri(self, _request):
                return SimpleNamespace(Uri="rtsp://admin:secret@192.168.10.20/sub?token=private#fragment")

        class Camera:
            def __init__(self, *_args):
                pass

            def create_media_service(self):
                return Media()

        with patch.dict(sys.modules, {"onvif": SimpleNamespace(ONVIFCamera=Camera)}):
            profiles = OnvifClient().profiles("192.168.10.20", 80, "admin", "secret")
        self.assertEqual(profiles[0]["resolution"], [1280, 720])
        self.assertEqual(profiles[0]["fps"], 15.0)
        self.assertEqual(profiles[0]["rtsp_url"], "rtsp://192.168.10.20/sub")


if __name__ == "__main__":
    unittest.main()
