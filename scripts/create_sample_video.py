"""Create a small local sample video for KSI-CCTV smoke validation.

The generated video is synthetic and intended to verify capture/dashboard flow.
It is not expected to be a reliable YOLO person-detection benchmark.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np


def create_sample_video(output: Path, seconds: float, fps: int, width: int, height: int) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(output), fourcc, fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open video writer: {output}")

    total_frames = max(1, int(seconds * fps))
    for idx in range(total_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (35, 35, 35)

        # Background zones
        cv2.rectangle(frame, (0, height - 90), (width, height), (55, 55, 55), -1)
        cv2.rectangle(frame, (40, 40), (width - 40, height - 120), (45, 60, 70), 2)

        # Person-like moving figure for visual validation
        t = idx / max(1, total_frames - 1)
        cx = int(120 + t * (width - 240))
        cy = height // 2 + 30
        cv2.circle(frame, (cx, cy - 90), 28, (70, 180, 220), -1)
        cv2.rectangle(frame, (cx - 28, cy - 60), (cx + 28, cy + 45), (60, 130, 220), -1)
        cv2.line(frame, (cx - 20, cy + 45), (cx - 45, cy + 100), (220, 220, 220), 8)
        cv2.line(frame, (cx + 20, cy + 45), (cx + 45, cy + 100), (220, 220, 220), 8)
        cv2.line(frame, (cx - 28, cy - 30), (cx - 75, cy + 15), (220, 220, 220), 7)
        cv2.line(frame, (cx + 28, cy - 30), (cx + 75, cy + 15), (220, 220, 220), 7)

        cv2.putText(
            frame,
            "KSI-CCTV synthetic E2E sample",
            (30, 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (240, 240, 240),
            2,
        )
        cv2.putText(
            frame,
            f"frame={idx + 1}/{total_frames}",
            (30, height - 35),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (220, 220, 220),
            2,
        )
        writer.write(frame)

    writer.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a synthetic KSI-CCTV sample video")
    parser.add_argument("--output", default="data/sample.mp4", help="Output video path")
    parser.add_argument("--seconds", type=float, default=6.0, help="Video duration")
    parser.add_argument("--fps", type=int, default=12, help="Frames per second")
    parser.add_argument("--width", type=int, default=640, help="Frame width")
    parser.add_argument("--height", type=int, default=480, help="Frame height")
    args = parser.parse_args()

    output = Path(args.output)
    create_sample_video(output, args.seconds, args.fps, args.width, args.height)
    print(f"Sample video created: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
