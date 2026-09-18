"""Regression tests for scripts/migrate_legacy_cameras.py.

Uses tmp_path and monkeypatched env only; never touches real config/*.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_legacy_cameras.py"

from src.cameras.credentials import CredentialStore
from src.cameras.models import CameraConfig
from src.cameras.repository import CameraRepository


def run_migration(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def write_source(path: Path, cameras: object) -> Path:
    path.write_text(json.dumps(cameras), encoding="utf-8")
    return path


BASIC_CAMERAS = [
    {
        "id": "cam_001",
        "name": "정문 입구",
        "location": "1층 정문",
        "stream_url": "rtsp://192.168.1.101:554/stream1",
        "fps": 30,
        "resolution": [1920, 1080],
        "enabled": True,
        "detection_type": "general",
        "enabled_events": ["fire", "smoke"],
        "zones": [
            {
                "id": "restricted",
                "type": "polygon",
                "points": [
                    {"x": 0.1, "y": 0.1},
                    {"x": 0.5, "y": 0.1},
                    {"x": 0.5, "y": 0.5},
                ],
            }
        ],
    },
    {
        "id": "cam_002",
        "name": "작업 현장 A",
        "location": "지하 2층 A구역",
        "stream_url": "rtsp://192.168.1.102:554/stream2",
        "fps": 25,
        "resolution": [1280, 720],
        "enabled": False,
        "detection_type": "construction",
    },
]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    source = write_source(tmp_path / "cameras.json", BASIC_CAMERAS)
    dest = tmp_path / "dest"
    before = set(tmp_path.rglob("*"))
    proc = run_migration("--source", str(source), "--dest", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert not dest.exists()
    assert set(tmp_path.rglob("*")) == before


def test_apply_creates_repository_with_preserved_fields(tmp_path: Path) -> None:
    source = write_source(tmp_path / "cameras.json", BASIC_CAMERAS)
    dest = tmp_path / "dest"
    proc = run_migration("--source", str(source), "--dest", str(dest), "--apply")
    assert proc.returncode == 0, proc.stderr
    _, cameras = CameraRepository(dest).list()
    by_id = {camera.id: camera for camera in cameras}
    assert set(by_id) == {"cam_001", "cam_002"}
    first = by_id["cam_001"]
    assert first.profile == "general"
    assert first.enabled is True
    assert first.fps == 30
    assert tuple(first.resolution or ()) == (1920, 1080)
    assert first.location == "1층 정문"
    assert first.enabled_events == ["fire", "smoke"]
    assert len(first.zones) == 1
    second = by_id["cam_002"]
    assert second.profile == "construction"
    assert second.enabled is False
    assert second.fps == 25


def test_apply_splits_userinfo_into_credential_store(tmp_path: Path) -> None:
    password = "s3cr3t-pw-9f2"
    source = write_source(
        tmp_path / "cameras.json",
        [
            {
                "id": "cam_009",
                "name": "창고",
                "stream_url": f"rtsp://installer:{password}@192.168.1.109:554/live",
                "enabled": True,
                "detection_type": "general",
            }
        ],
    )
    dest = tmp_path / "dest"
    proc = run_migration("--source", str(source), "--dest", str(dest), "--apply")
    assert proc.returncode == 0, proc.stderr
    _, cameras = CameraRepository(dest).list()
    assert len(cameras) == 1
    stored: CameraConfig = cameras[0]
    assert "@" not in stored.rtsp_url
    assert "installer" not in stored.rtsp_url
    credential = CredentialStore(dest).get("camera:cam_009")
    assert credential is not None
    assert credential.get("username") == "installer"
    assert credential.get("password") == password
    assert credential.get("target_scope")
    combined = proc.stdout + proc.stderr
    assert password not in combined


def test_dry_run_output_masks_password(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("KSI_MIGRATE_TEST_PW_7Q", "dry-run-pw-4d8")
    source = write_source(
        tmp_path / "cameras.json",
        [
            {
                "id": "cam_007",
                "name": "주차장",
                "stream_url": "rtsp://watcher:${KSI_MIGRATE_TEST_PW_7Q}@192.168.1.107/live",
                "enabled": True,
            }
        ],
    )
    dest = tmp_path / "dest"
    proc = run_migration("--source", str(source), "--dest", str(dest))
    assert proc.returncode == 0, proc.stderr
    assert not dest.exists()
    combined = proc.stdout + proc.stderr
    assert "dry-run-pw-4d8" not in combined
    assert "***" in combined


def test_unresolved_env_entry_is_skipped(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("KSI_MIGRATE_MISSING_URL_ZZ", raising=False)
    source = write_source(
        tmp_path / "cameras.json",
        [
            {
                "id": "cam_env",
                "name": "미주입",
                "stream_url": "${KSI_MIGRATE_MISSING_URL_ZZ}",
                "enabled": True,
            },
            {
                "id": "cam_ok",
                "name": "정상",
                "stream_url": "rtsp://192.168.1.111:554/live",
                "enabled": True,
            },
        ],
    )
    dest = tmp_path / "dest"
    proc = run_migration("--source", str(source), "--dest", str(dest), "--apply")
    assert proc.returncode == 0, proc.stderr
    _, cameras = CameraRepository(dest).list()
    assert [camera.id for camera in cameras] == ["cam_ok"]
    assert "cam_env" in proc.stdout


def test_non_empty_dest_is_rejected_with_exit_2(tmp_path: Path) -> None:
    source = write_source(tmp_path / "cameras.json", BASIC_CAMERAS)
    dest = tmp_path / "dest"
    first = run_migration("--source", str(source), "--dest", str(dest), "--apply")
    assert first.returncode == 0, first.stderr
    second = run_migration("--source", str(source), "--dest", str(dest), "--apply")
    assert second.returncode == 2
    assert "관리 저장소가 비어있지 않음" in second.stderr


def test_missing_source_is_fatal_exit_2(tmp_path: Path) -> None:
    proc = run_migration(
        "--source", str(tmp_path / "nope.json"), "--dest", str(tmp_path / "dest")
    )
    assert proc.returncode == 2
