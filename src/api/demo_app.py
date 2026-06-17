"""데모/시연 전용 ASGI 앱 — UI/UX 확인(scripts/run_web.sh KSI_CCTV_DEMO=1)에서만 사용.

실제 탐지 파이프라인 대신 합성 파이프라인을 주입해, 카메라 라이브 텔레메트리(/api/cameras/telemetry)와
파이프라인 제어(/api/control/*)가 데이터를 가진 채 동작하는 것을 브라우저에서 보여준다.
운영(데몬)은 src/run.py가 실제 파이프라인을 mount하므로 이 모듈은 쓰지 않는다(프로덕션 무관).
"""
from __future__ import annotations

from typing import Any

from src.api.app import create_app


class _DemoPipeline:
    """합성 파이프라인 — 텔레메트리/제어 시연용(실제 프레임 처리는 없음)."""

    def __init__(self) -> None:
        self.is_running = True

    def start(self) -> None:
        self.is_running = True

    def stop(self) -> None:
        self.is_running = False

    def get_status(self) -> dict[str, Any]:
        if not self.is_running:
            return {"running": False, "cameras": {}}
        return {
            "running": True,
            "cameras": {
                # 송출 중인 합성 카메라(연결·정상 FPS)
                "cam_001": {
                    "connected": True,
                    "fps": 24.9,
                    "queue_size": 1,
                    "frame_count": 12345,
                    "dropped_frames": 2,
                    "is_stale": False,
                    "seconds_since_last_frame": 0.1,
                    "reconnect_attempts": 0,
                    "consecutive_failures": 0,
                },
                # 비활성/끊김 카메라(지연 상태 — UI의 stale 표시 시연)
                "cam_002": {
                    "connected": False,
                    "fps": 0.0,
                    "queue_size": 0,
                    "frame_count": 0,
                    "dropped_frames": 0,
                    "is_stale": True,
                    "seconds_since_last_frame": None,
                    "reconnect_attempts": 4,
                    "consecutive_failures": 4,
                },
            },
        }


app = create_app(pipeline=_DemoPipeline())
