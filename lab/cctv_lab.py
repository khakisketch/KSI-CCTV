#!/usr/bin/env python3
"""Docker Compose controller for the isolated virtual CCTV lab."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

MIN_CAMERAS = 1
MAX_CAMERAS = 16
PROJECT_NAME = "ksi-cctv-lab"


def camera_path(camera: int) -> str:
    validate_camera(camera)
    return f"cam_{camera:03d}"


def publisher_service(camera: int) -> str:
    validate_camera(camera)
    return f"publisher_{camera:03d}"


def validate_camera(camera: int) -> None:
    if not MIN_CAMERAS <= camera <= MAX_CAMERAS:
        raise ValueError(f"카메라 번호는 {MIN_CAMERAS}~{MAX_CAMERAS}이어야 합니다: {camera}")


def validate_count(count: int) -> None:
    if not MIN_CAMERAS <= count <= MAX_CAMERAS:
        raise ValueError(f"카메라 수는 {MIN_CAMERAS}~{MAX_CAMERAS}이어야 합니다: {count}")


def validate_lab_files(root: Path) -> list[str]:
    """Validate files without requiring Docker, YAML libraries, or a daemon."""
    problems: list[str] = []
    compose = root / "lab" / "docker-compose.yml"
    mediamtx = root / "lab" / "config" / "mediamtx.yml"
    sample = root / "data" / "sample.mp4"
    for path in (compose, mediamtx, sample):
        if not path.is_file():
            problems.append(f"필수 파일이 없습니다: {path.relative_to(root)}")
    if compose.is_file():
        text = compose.read_text(encoding="utf-8")
        for camera in range(MIN_CAMERAS, MAX_CAMERAS + 1):
            service = publisher_service(camera)
            path = camera_path(camera)
            if f"{service}:" not in text or path not in text:
                problems.append(f"Compose에 {service}/{path} 정의가 없습니다.")
    if mediamtx.is_file():
        text = mediamtx.read_text(encoding="utf-8")
        for camera in range(MIN_CAMERAS, MAX_CAMERAS + 1):
            if f"{camera_path(camera)}:" not in text:
                problems.append(f"MediaMTX 설정에 {camera_path(camera)} 경로가 없습니다.")
        if "cam_usb_lab:" not in text:
            problems.append("MediaMTX 설정에 USB lab path가 없습니다.")
    return problems


@dataclass
class LabController:
    root: Path

    @property
    def compose_file(self) -> Path:
        return self.root / "lab" / "docker-compose.yml"

    def compose_command(self, *args: str) -> list[str]:
        return [
            "docker", "compose", "--project-name", PROJECT_NAME,
            "--project-directory", str(self.root / "lab"), "-f", str(self.compose_file), *args,
        ]

    def run_compose(self, *args: str, capture_output: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.compose_command(*args), check=False, text=True, stdin=subprocess.DEVNULL,
            capture_output=capture_output,
        )

    def start(self, count: int) -> subprocess.CompletedProcess[str]:
        validate_count(count)
        services = ["mediamtx", *(publisher_service(i) for i in range(1, count + 1))]
        return self.run_compose("up", "-d", "--remove-orphans", *services)

    def stop(self) -> subprocess.CompletedProcess[str]:
        return self.run_compose("down", "--remove-orphans")

    def status(self) -> subprocess.CompletedProcess[str]:
        return self.run_compose("ps", "--format", "json", capture_output=True)

    def stop_camera(self, camera: int) -> subprocess.CompletedProcess[str]:
        return self.run_compose("stop", publisher_service(camera))

    def restart_camera(self, camera: int) -> subprocess.CompletedProcess[str]:
        return self.run_compose("restart", publisher_service(camera))

    def fault_cycle(
        self, camera: int, down_sec: float
    ) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
        validate_camera(camera)
        if not 1.0 <= down_sec <= 300.0:
            raise ValueError("중단 시간은 1~300초여야 합니다.")
        stopped = self.stop_camera(camera)
        if stopped.returncode:
            return stopped, stopped
        time.sleep(down_sec)
        return stopped, self.restart_camera(camera)

    def reset(self, count: int) -> tuple[subprocess.CompletedProcess[str], subprocess.CompletedProcess[str]]:
        validate_count(count)
        return self.run_compose("down", "--volumes", "--remove-orphans"), self.start(count)


def print_result(result: subprocess.CompletedProcess[str], success: str) -> int:
    if result.returncode == 0:
        print(success)
        if result.stdout:
            print(result.stdout.rstrip())
        return 0
    print("실행에 실패했습니다. Docker Desktop/Engine 상태와 로그를 확인하세요.", file=sys.stderr)
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    return result.returncode or 1


def doctor(controller: LabController) -> int:
    problems = validate_lab_files(controller.root)
    if problems:
        print("랩 파일 검사 실패:", file=sys.stderr)
        print("\n".join(f"- {problem}" for problem in problems), file=sys.stderr)
        return 1
    print("[OK] 랩 파일과 sample.mp4를 확인했습니다.")
    if shutil.which("docker") is None:
        print("[경고] docker 명령을 찾지 못했습니다. Docker를 설치한 뒤 다시 실행하세요.")
        return 0
    result = controller.run_compose("config", "-q", capture_output=True)
    if result.returncode:
        print("[실패] Docker Compose 설정 검증에 실패했습니다.", file=sys.stderr)
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        return result.returncode
    print("[OK] Docker Compose 설정이 유효합니다.")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="하드웨어 없는 KSI CCTV 가상 랩 제어기")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="1~16개 가상 카메라를 시작합니다")
    start.add_argument("count", type=int, nargs="?", default=1)
    commands.add_parser("stop", help="랩의 모든 컨테이너를 중지하고 제거합니다")
    commands.add_parser("status", help="컨테이너 상태를 JSON으로 표시합니다")
    for name, help_text in (("stop-camera", "카메라 한 대를 중지합니다"), ("restart-camera", "카메라 한 대를 재시작합니다")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("camera", type=int)
    fault_cycle = commands.add_parser("fault-cycle", help="카메라 publisher를 중단 후 재기동해 복구를 검증합니다")
    fault_cycle.add_argument("camera", type=int)
    fault_cycle.add_argument("--down-sec", type=float, default=35.0)
    reset = commands.add_parser("reset", help="볼륨을 제거하고 지정한 수의 카메라로 다시 시작합니다")
    reset.add_argument("count", type=int, nargs="?", default=1)
    commands.add_parser("doctor", help="Docker 없이 파일을, 가능하면 Compose도 검사합니다")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    controller = LabController(Path(__file__).resolve().parents[1])
    try:
        if args.command == "doctor":
            return doctor(controller)
        if args.command == "start":
            return print_result(controller.start(args.count), f"가상 카메라 {args.count}대를 시작했습니다.")
        if args.command == "stop":
            return print_result(controller.stop(), "가상 CCTV 랩을 중지했습니다.")
        if args.command == "status":
            return print_result(controller.status(), "가상 CCTV 랩 상태입니다.")
        if args.command == "stop-camera":
            return print_result(controller.stop_camera(args.camera), f"{camera_path(args.camera)}를 중지했습니다.")
        if args.command == "restart-camera":
            return print_result(controller.restart_camera(args.camera), f"{camera_path(args.camera)}를 재시작했습니다.")
        if args.command == "fault-cycle":
            stopped, restarted = controller.fault_cycle(args.camera, args.down_sec)
            if stopped.returncode:
                return print_result(stopped, "")
            return print_result(restarted, f"{camera_path(args.camera)} fault cycle을 완료했습니다. 엔진에서 stale/복구 상태를 확인하세요.")
        stopped, started = controller.reset(args.count)
        if stopped.returncode:
            return print_result(stopped, "")
        return print_result(started, f"랩을 초기화하고 가상 카메라 {args.count}대를 시작했습니다.")
    except ValueError as error:
        print(f"입력 오류: {error}", file=sys.stderr)
        return 2
    except FileNotFoundError:
        print("docker 명령을 찾지 못했습니다. Docker를 설치하거나 PATH를 확인하세요.", file=sys.stderr)
        return 127


if __name__ == "__main__":
    raise SystemExit(main())
