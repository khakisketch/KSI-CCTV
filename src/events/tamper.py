"""Sustained camera occlusion/blur detection with recovery hysteresis."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np


@dataclass(frozen=True)
class TamperTransition:
    camera_id: str
    event_type: str
    reason: str
    metrics: dict[str, float]


@dataclass
class _TamperState:
    samples: int = 0
    baseline_sharpness: float | None = None
    suspected_since: float | None = None
    recovery_since: float | None = None
    active: bool = False
    reason: str = ""
    last_sample_at: float | None = None


class TamperMonitor:
    """Detect persistent flat-dark occlusion or sharpness collapse."""

    def __init__(
        self,
        duration_sec: float = 5.0,
        recovery_sec: float = 3.0,
        warmup_samples: int = 5,
        max_sample_gap_sec: float = 2.0,
    ):
        self.duration_sec = max(0.1, float(duration_sec))
        self.recovery_sec = max(0.1, float(recovery_sec))
        self.warmup_samples = max(1, int(warmup_samples))
        self.max_sample_gap_sec = max(0.1, float(max_sample_gap_sec))
        self._states: dict[str, _TamperState] = {}
        self._lock = threading.RLock()

    def update(
        self,
        camera_id: str,
        frame: np.ndarray,
        now: float | None = None,
    ) -> TamperTransition | None:
        current_time = time.monotonic() if now is None else float(now)
        metrics = self._metrics(frame)
        with self._lock:
            state = self._states.setdefault(camera_id, _TamperState())
            if (
                state.last_sample_at is not None
                and current_time - state.last_sample_at > self.max_sample_gap_sec
            ):
                state.suspected_since = None
                state.recovery_since = None
                if not state.active:
                    state.reason = ""
            state.last_sample_at = current_time
            state.samples += 1
            reason = self._suspicion_reason(state, metrics)

            if reason:
                state.recovery_since = None
                if state.suspected_since is None or state.reason != reason:
                    state.suspected_since = current_time
                    state.reason = reason
                if (
                    not state.active
                    and state.samples >= self.warmup_samples
                    and current_time - state.suspected_since >= self.duration_sec
                ):
                    state.active = True
                    return TamperTransition(camera_id, "tamper", reason, metrics)
                return None

            state.suspected_since = None
            state.reason = ""
            if state.active:
                if state.recovery_since is None:
                    state.recovery_since = current_time
                elif current_time - state.recovery_since >= self.recovery_sec:
                    state.active = False
                    state.recovery_since = None
                    self._update_baseline(state, metrics["sharpness"])
                    return TamperTransition(camera_id, "tamper_recovered", "normal", metrics)
                return None

            state.recovery_since = None
            self._update_baseline(state, metrics["sharpness"])
            return None

    def state(self, camera_id: str) -> str:
        with self._lock:
            state = self._states.get(camera_id)
            if state is None:
                return "normal"
            if state.active:
                return "tampered"
            return "suspected" if state.suspected_since is not None else "normal"

    def reset(self, camera_id: str | None = None) -> None:
        with self._lock:
            if camera_id is None:
                self._states.clear()
            else:
                self._states.pop(camera_id, None)

    def _suspicion_reason(self, state: _TamperState, metrics: dict[str, float]) -> str:
        if metrics["brightness"] < 12.0 and metrics["contrast"] < 8.0:
            return "flat_dark"
        if metrics["contrast"] < 2.0:
            return "flat_frame"
        baseline = state.baseline_sharpness
        if (
            state.samples >= self.warmup_samples
            and baseline is not None
            and baseline >= 20.0
            and metrics["sharpness"] < max(5.0, baseline * 0.10)
        ):
            return "sharpness_collapse"
        return ""

    @staticmethod
    def _metrics(frame: np.ndarray) -> dict[str, float]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (160, 120))
        gray_any: Any = gray
        return {
            "brightness": float(np.mean(gray_any)),
            "contrast": float(np.std(gray_any)),
            "sharpness": float(cv2.Laplacian(gray, cv2.CV_64F).var()),
        }

    @staticmethod
    def _update_baseline(state: _TamperState, sharpness: float) -> None:
        if state.baseline_sharpness is None:
            state.baseline_sharpness = sharpness
        else:
            state.baseline_sharpness = state.baseline_sharpness * 0.95 + sharpness * 0.05
