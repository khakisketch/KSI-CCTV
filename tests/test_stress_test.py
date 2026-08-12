"""Focused tests for the software-only stress validation gate."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "stress_test", Path(__file__).resolve().parents[1] / "scripts" / "stress_test.py"
)
assert SPEC and SPEC.loader
stress_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stress_test)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "max_failed_frames": 0,
        "max_failure_rate": None,
        "min_fps": None,
        "max_p95_latency_ms": None,
        "max_rss_mb": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_latency_metrics_include_interpolated_percentiles() -> None:
    metrics = stress_test._latency_metrics([10.0, 20.0, 30.0, 40.0, 50.0])

    assert metrics == {"min": 10.0, "max": 50.0, "avg": 30.0, "p50": 30.0, "p95": 48.0, "p99": 49.6}


def test_gate_failures_reports_each_exceeded_threshold() -> None:
    summary = {
        "frames_processed": 10,
        "failed_frames": 2,
        "fps_actual": 4.0,
        "total_ms": {"p95": 120.0},
        "process_rss_mb": {"peak": 256.0},
    }

    failures = stress_test._gate_failures(
        summary,
        _args(
            max_failed_frames=1,
            max_failure_rate=0.1,
            min_fps=5,
            max_p95_latency_ms=100,
            max_rss_mb=200,
        ),
    )

    assert failures == [
        "failed frames 2 exceeds 1",
        "failure rate 20.00% exceeds 10.00%",
        "fps 4.00 is below 5.00",
        "p95 latency 120.0ms exceeds 100.0ms",
        "peak RSS 256.0MB exceeds 200.0MB",
    ]


def test_gate_fails_when_no_frames_are_processed() -> None:
    summary = {
        "frames_processed": 0,
        "failed_frames": 0,
        "fps_actual": 0.0,
        "total_ms": {"p95": 0.0},
        "process_rss_mb": {"peak": None},
    }

    assert stress_test._gate_failures(summary, _args()) == ["no frames were processed"]
