"""Pure, deterministic camera execution-plan compiler (not a runtime applier)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .models import CameraConfig


@dataclass(frozen=True)
class ExecutionPlan:
    revision: int
    cameras: tuple[dict, ...]

    def to_dict(self) -> dict:
        return asdict(self)


def compile_plan(revision: int, cameras: list[CameraConfig]) -> ExecutionPlan:
    return ExecutionPlan(revision=revision, cameras=tuple(
        {
            "id": c.id,
            "rtsp_url": c.rtsp_url,
            "enabled": c.enabled,
            "transport": c.transport,
            "input_fps": c.fps,
            "resolution": c.resolution,
            "profile": c.profile,
            "events": tuple(c.enabled_events),
            "zones": tuple(zone.model_dump() for zone in c.zones),
            "priority": "high" if any(e in {"fire", "smoke", "fall", "fight"} for e in c.enabled_events) else "normal",
        }
        for c in sorted(cameras, key=lambda item: item.id)
    ))
