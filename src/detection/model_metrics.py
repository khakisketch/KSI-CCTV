"""Shared inference metrics for detection and behavior models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Recent samples retained for p50/p95 latency calculations.
_LATENCY_WINDOW = 100


@dataclass
class ModelMetrics:
    """Per-model runtime inference metrics."""

    name: str
    inference_count: int = 0
    success_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    total_latency_ms: float = 0.0
    last_latency_ms: float = -1.0
    _latency_window: deque[float] = field(default_factory=lambda: deque(maxlen=_LATENCY_WINDOW))

    def record_success(self, latency_ms: float) -> None:
        self.inference_count += 1
        self.success_count += 1
        self.consecutive_errors = 0
        self.total_latency_ms += latency_ms
        self.last_latency_ms = latency_ms
        self._latency_window.append(latency_ms)

    def record_error(self) -> None:
        self.inference_count += 1
        self.error_count += 1
        self.consecutive_errors += 1

    @property
    def avg_latency_ms(self) -> float:
        if self.success_count == 0:
            return -1.0
        return self.total_latency_ms / self.success_count

    @property
    def p50_latency_ms(self) -> float:
        samples = list(self._latency_window)
        if not samples:
            return -1.0
        sorted_s = sorted(samples)
        mid = len(sorted_s) // 2
        if len(sorted_s) % 2 == 0:
            return (sorted_s[mid - 1] + sorted_s[mid]) / 2.0
        return sorted_s[mid]

    @property
    def p95_latency_ms(self) -> float:
        samples = list(self._latency_window)
        if not samples:
            return -1.0
        sorted_s = sorted(samples)
        return sorted_s[max(0, int(len(sorted_s) * 0.95) - 1)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "inference_count": self.inference_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "consecutive_errors": self.consecutive_errors,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "last_latency_ms": round(self.last_latency_ms, 2),
        }
