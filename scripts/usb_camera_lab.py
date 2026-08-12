#!/usr/bin/env python3
"""Publish one local V4L2 camera to an isolated lab RTSP path.

This is a developer bench helper, not a managed production camera adapter. The
production Appliance must continue to receive validated network RTSP sources.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_DEVICE = "/dev/video0"
DEFAULT_RTSP_URL = "rtsp://127.0.0.1:8554/cam_usb_lab"
_DEVICE_RE = re.compile(r"^/dev/video[0-9]+$")
_PIXEL_FORMAT_RE = re.compile(r"^[A-Za-z0-9_]+$")
_SIZE_RE = re.compile(r"^[1-9][0-9]{1,4}x[1-9][0-9]{1,4}$")


def validate_device(device: str) -> Path:
    path = Path(device)
    if not _DEVICE_RE.fullmatch(str(path)):
        raise ValueError("장치는 /dev/videoN 형식이어야 합니다.")
    if not path.exists():
        raise ValueError(f"V4L2 장치를 찾을 수 없습니다: {path}")
    if not path.is_char_device():
        raise ValueError(f"V4L2 문자 장치가 아닙니다: {path}")
    return path


def validate_rtsp_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "rtsp" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("USB lab publisher는 loopback rtsp:// URL만 사용할 수 있습니다.")
    if parsed.path != "/cam_usb_lab" or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("USB lab RTSP path는 인증 없는 /cam_usb_lab 이어야 합니다.")
    return url


def build_command(
    *,
    device: str,
    rtsp_url: str,
    video_size: str,
    fps: int,
    input_format: str | None = None,
) -> list[str]:
    if not _SIZE_RE.fullmatch(video_size):
        raise ValueError("해상도는 WIDTHxHEIGHT 형식이어야 합니다.")
    if not 1 <= fps <= 60:
        raise ValueError("FPS는 1~60이어야 합니다.")
    if input_format and not _PIXEL_FORMAT_RE.fullmatch(input_format):
        raise ValueError("input format은 영숫자와 밑줄만 사용할 수 있습니다.")
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-f",
        "v4l2",
        "-video_size",
        video_size,
        "-framerate",
        str(fps),
    ]
    if input_format:
        command.extend(["-input_format", input_format])
    command.extend([
        "-i",
        device,
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-g",
        str(max(2, fps * 2)),
        "-pix_fmt",
        "yuv420p",
        "-f",
        "rtsp",
        "-rtsp_transport",
        "tcp",
        rtsp_url,
    ])
    return command


def list_devices() -> list[str]:
    return [str(path) for path in sorted(Path("/dev").glob("video[0-9]*")) if path.is_char_device()]


def doctor() -> int:
    devices = list_devices()
    if devices:
        print("[OK] V4L2 장치: " + ", ".join(devices))
    else:
        print("[실패] /dev/videoN V4L2 장치를 찾지 못했습니다.", file=sys.stderr)
        return 1
    if shutil.which("ffmpeg") is None:
        print("[실패] ffmpeg를 찾지 못했습니다.", file=sys.stderr)
        return 1
    print("[OK] ffmpeg를 찾았습니다.")
    print("[안내] 이 도구는 lab 전용 RTSP publisher이며 production USB 지원이 아닙니다.")
    return 0


def publish(command: list[str]) -> int:
    process = subprocess.Popen(command, start_new_session=True)

    def stop(_signum: int, _frame: object) -> None:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)

    previous_int = signal.signal(signal.SIGINT, stop)
    previous_term = signal.signal(signal.SIGTERM, stop)
    try:
        return process.wait()
    finally:
        signal.signal(signal.SIGINT, previous_int)
        signal.signal(signal.SIGTERM, previous_term)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KSI-CCTV USB/V4L2 lab RTSP publisher")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="V4L2 장치와 FFmpeg 준비 상태를 확인합니다")
    publish_command = commands.add_parser("publish", help="USB 프레임을 loopback RTSP lab path로 발행합니다")
    publish_command.add_argument("--device", default=DEFAULT_DEVICE)
    publish_command.add_argument("--rtsp-url", default=DEFAULT_RTSP_URL)
    publish_command.add_argument("--video-size", default="1280x720")
    publish_command.add_argument("--fps", type=int, default=15)
    publish_command.add_argument("--input-format", default=None)
    publish_command.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "doctor":
        return doctor()
    try:
        device = validate_device(args.device)
        rtsp_url = validate_rtsp_url(args.rtsp_url)
        command = build_command(
            device=str(device),
            rtsp_url=rtsp_url,
            video_size=args.video_size,
            fps=args.fps,
            input_format=args.input_format,
        )
    except ValueError as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(" ".join(command))
        return 0
    if shutil.which("ffmpeg") is None:
        print("ffmpeg를 찾지 못했습니다.", file=sys.stderr)
        return 127
    return publish(command)


if __name__ == "__main__":
    raise SystemExit(main())
