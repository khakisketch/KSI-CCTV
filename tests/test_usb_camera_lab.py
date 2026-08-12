from __future__ import annotations

from pathlib import Path

import pytest

from scripts.usb_camera_lab import build_command, validate_device, validate_rtsp_url


def test_usb_lab_command_is_loopback_rtsp_and_shell_free() -> None:
    command = build_command(
        device="/dev/video0",
        rtsp_url="rtsp://127.0.0.1:8554/cam_usb_lab",
        video_size="1280x720",
        fps=15,
        input_format="mjpeg",
    )

    assert command[:5] == ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-f"]
    assert "-f" in command and "v4l2" in command
    assert command[-1] == "rtsp://127.0.0.1:8554/cam_usb_lab"
    assert "shell" not in " ".join(command)


@pytest.mark.parametrize(
    "url",
    [
        "rtsp://10.0.0.5:8554/cam_usb_lab",
        "rtsps://127.0.0.1:8554/cam_usb_lab",
        "rtsp://127.0.0.1:8554/not-usb",
        "rtsp://user:password@127.0.0.1:8554/cam_usb_lab",
    ],
)
def test_usb_lab_rejects_non_lab_rtsp_urls(url: str) -> None:
    with pytest.raises(ValueError):
        validate_rtsp_url(url)


def test_usb_lab_rejects_non_video_device_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        validate_device(str(tmp_path / "not-a-camera"))


@pytest.mark.parametrize("size,fps", [("0x720", 15), ("1280x720", 0), ("1280x720", 61)])
def test_usb_lab_command_rejects_invalid_media_settings(size: str, fps: int) -> None:
    with pytest.raises(ValueError):
        build_command(
            device="/dev/video0",
            rtsp_url="rtsp://127.0.0.1:8554/cam_usb_lab",
            video_size=size,
            fps=fps,
        )
