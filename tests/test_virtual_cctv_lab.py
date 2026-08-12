from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from lab.cctv_lab import (
    LabController,
    camera_path,
    publisher_service,
    validate_count,
    validate_lab_files,
)
from src.api.app import create_app
from src.cameras import MediaMTXClient, RtspProbe

ROOT = Path(__file__).resolve().parents[1]


def test_camera_names_are_deterministic_at_boundaries() -> None:
    assert camera_path(1) == "cam_001"
    assert camera_path(16) == "cam_016"
    assert publisher_service(7) == "publisher_007"


@pytest.mark.parametrize("count", [0, 17, -1])
def test_camera_count_is_bounded(count: int) -> None:
    with pytest.raises(ValueError, match="1~16"):
        validate_count(count)


def test_start_command_selects_only_requested_publishers() -> None:
    controller = LabController(ROOT)
    command = controller.compose_command(
        "up", "-d", "--remove-orphans", "mediamtx", "publisher_001", "publisher_002"
    )
    assert command[:4] == ["docker", "compose", "--project-name", "ksi-cctv-lab"]
    assert command[-5:] == ["-d", "--remove-orphans", "mediamtx", "publisher_001", "publisher_002"]
    assert "shell" not in " ".join(command)


def test_compose_commands_do_not_use_a_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="[]", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = LabController(ROOT).status()
    assert result.returncode == 0
    assert calls[0][1]["stdin"] is subprocess.DEVNULL
    assert calls[0][1].get("shell") is None
    assert calls[0][0][-3:] == ["ps", "--format", "json"]


def test_fault_cycle_stops_then_restarts_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    stopped, restarted = LabController(ROOT).fault_cycle(1, 2)

    assert stopped.returncode == 0
    assert restarted.returncode == 0
    assert calls[0][-2:] == ["stop", "publisher_001"]
    assert calls[1][-2:] == ["restart", "publisher_001"]


@pytest.mark.parametrize("seconds", [0, 301])
def test_fault_cycle_has_bounded_downtime(seconds: float) -> None:
    with pytest.raises(ValueError, match="1~300"):
        LabController(ROOT).fault_cycle(1, seconds)


def test_lab_files_define_all_cameras_without_docker() -> None:
    assert validate_lab_files(ROOT) == []


@pytest.mark.skipif(
    os.environ.get("KSI_CCTV_LAB_INTEGRATION") != "1",
    reason="Docker integration test is opt-in: KSI_CCTV_LAB_INTEGRATION=1",
)
def test_docker_compose_config_is_valid() -> None:
    controller = LabController(ROOT)
    result = controller.run_compose("config", "-q", capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(
    os.environ.get("KSI_CCTV_LAB_INTEGRATION") != "1",
    reason="Docker integration test is opt-in: KSI_CCTV_LAB_INTEGRATION=1",
)
def test_managed_camera_api_first_frame_gateway_and_delete() -> None:
    class Pipeline:
        def apply_camera(self, *_args, **_kwargs):
            return {"applied": True}

        def remove_camera(self, _camera_id):
            return True

        def get_status(self):
            return {"cameras": {}}

    controller = LabController(ROOT)
    started = controller.start(1)
    assert started.returncode == 0, started.stderr
    gateway = MediaMTXClient("http://127.0.0.1:9997")
    key = "lab-integration-key"
    try:
        with tempfile.TemporaryDirectory() as temp:
            client = TestClient(create_app(
                camera_directory=Path(temp),
                pipeline=Pipeline(),
                rtsp_probe=RtspProbe(allow_loopback=True),
                mediamtx=gateway,
            ))
            headers = {"X-API-Key": key}
            body = {
                "id": "e2e_managed",
                "name": "Lab managed camera",
                "rtsp_url": "rtsp://127.0.0.1:8554/cam_001",
                "profile": "general",
                "enabled_events": ["fire"],
            }
            with patch.dict(os.environ, {"KSI_CCTV_API_KEY": key}):
                deadline = time.monotonic() + 20
                while True:
                    tested = client.post(
                        "/api/cameras/test",
                        headers=headers,
                        json={"rtsp_url": body["rtsp_url"], "verify_stream": True},
                    )
                    if tested.json().get("code") == "first_frame" or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                created = client.post("/api/cameras", headers=headers, json=body)
                deadline = time.monotonic() + 20
                while True:
                    relayed = RtspProbe(allow_loopback=True).run(
                        "rtsp://127.0.0.1:8554/e2e_managed",
                        timeout=5,
                        verify_stream=True,
                    )
                    if relayed.get("code") == "first_frame" or time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
                deleted = client.delete("/api/cameras/e2e_managed", headers=headers)

            assert tested.json()["code"] == "first_frame"
            assert created.status_code == 201, created.text
            assert created.json()["gateway_synced"] is True
            assert relayed["code"] == "first_frame"
            assert deleted.status_code == 200, deleted.text
    finally:
        with contextlib.suppress(Exception):
            gateway.delete("e2e_managed")
        controller.stop()
