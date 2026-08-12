from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "appliance_soak", Path(__file__).resolve().parents[1] / "scripts" / "appliance_soak.py"
)
assert SPEC and SPEC.loader
appliance_soak = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(appliance_soak)


def _args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "min_health_pass_rate": 1.0,
        "min_readiness_pass_rate": 1.0,
        "expected_cameras": 2,
        "max_disk_percent": 90.0,
        "max_cpu_percent": None,
        "max_memory_percent": None,
        "require_cpu_metrics": False,
        "require_memory_metrics": False,
        "max_gpu_temperature_c": 85.0,
        "require_gpu_metrics": True,
        "min_camera_fps": 5.0,
        "max_camera_drop_rate": 0.05,
        "require_recovery": True,
        "max_model_error_rate": None,
        "max_model_consecutive_errors": None,
        "max_vlm_latency_ms": None,
        "require_post_restart_recovery": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _sample(*, ready: bool, frame_count: int, dropped_frames: int, fps: float = 12.0) -> dict[str, object]:
    return {
        "health": {"ok": True},
        "readiness": {"ok": ready, "enabled_count": 2, "healthy_count": 2 if ready else 0},
        "system": {
            "disk_percent": 60.0,
            "cpu_percent": 50.0,
            "memory_percent": 55.0,
            "gpu_temperature": 70.0,
        },
        "ai": {
            "max_vlm_latency_ms": 120.0,
            "max_model_error_rate": 0.01,
            "max_model_consecutive_errors": 0.0,
        },
        "cameras": {
            "camera-hash": {
                "fps": fps,
                "frame_count": frame_count,
                "dropped_frames": dropped_frames,
            }
        },
    }


def test_collect_sample_redacts_camera_identifier_and_sources() -> None:
    responses = {
        "/api/health": {
            "status": "healthy",
            "system": {"disk_percent": 42.0},
            "components": [
                {
                    "name": "model_registry",
                    "metadata": {
                        "models": {
                            "customer-model": {
                                "inference_count": 10,
                                "error_count": 1,
                                "consecutive_errors": 0,
                            }
                        }
                    },
                },
                {"name": "vlm", "metadata": {"avg_latency_ms": 50.0}},
            ],
        },
        "/api/system": {"metrics": {"disk_percent": 42.0, "cpu_percent": 20.0}},
        "/api/cameras/readiness": {
            "ready": True,
            "enabled_cameras": ["front-door"],
            "healthy_cameras": ["front-door"],
        },
        "/api/cameras/health": {
            "cameras": {
                "front-door": {
                    "connected": True,
                    "fps": 10.0,
                    "frame_count": 10,
                    "dropped_frames": 1,
                    "stream_url": "rtsp://admin:secret@camera/private",
                }
            }
        },
    }

    sample = appliance_soak.collect_sample(lambda path: (responses[path], None))

    serialized = str(sample)
    assert "front-door" not in serialized
    assert "secret" not in serialized
    assert "customer-model" not in serialized
    assert sample["readiness"]["enabled_count"] == 1
    assert sample["cameras"]


def test_summary_and_gate_require_real_recovery_and_metrics() -> None:
    summary = appliance_soak.summarize(
        [
            _sample(ready=False, frame_count=100, dropped_frames=2),
            _sample(ready=True, frame_count=200, dropped_frames=4),
        ]
    )

    assert summary["observed_readiness_recovery"] is True
    assert summary["camera_drop_rate"] == 0.02
    assert appliance_soak.gate_failures(summary, _args()) == [
        "camera readiness pass rate 50.00% is below 100.00%"
    ]


def test_gate_reports_missing_required_metrics() -> None:
    sample = _sample(ready=True, frame_count=0, dropped_frames=0)
    sample["system"] = {"gpu_temperature": 70.0}
    sample["ai"] = {}
    summary = appliance_soak.summarize([sample])

    failures = appliance_soak.gate_failures(
        summary,
        _args(
            require_recovery=False,
            max_cpu_percent=80.0,
            max_memory_percent=80.0,
            require_cpu_metrics=True,
            require_memory_metrics=True,
            max_model_error_rate=0.1,
            max_model_consecutive_errors=1.0,
            max_vlm_latency_ms=500.0,
        ),
    )

    assert "disk usage threshold requested but disk metrics were unavailable" in failures
    assert "CPU usage threshold requested but metrics were unavailable" in failures
    assert "memory usage threshold requested but metrics were unavailable" in failures
    assert "CPU metrics were required but unavailable" in failures
    assert "memory metrics were required but unavailable" in failures
    assert "camera drop-rate threshold requested but frame counters were unavailable" in failures
    assert "model error-rate threshold requested but model metrics were unavailable" in failures
    assert "model consecutive-error threshold requested but model metrics were unavailable" in failures
    assert "VLM latency threshold requested but VLM metrics were unavailable" in failures


def test_gate_reports_exceeded_resource_and_ai_thresholds() -> None:
    sample = _sample(ready=True, frame_count=10, dropped_frames=0)
    sample["system"] = {"disk_percent": 95.0, "cpu_percent": 81.0, "memory_percent": 82.0, "gpu_temperature": 70.0}
    sample["ai"] = {
        "max_model_error_rate": 0.2,
        "max_model_consecutive_errors": 3.0,
        "max_vlm_latency_ms": 501.0,
    }
    summary = appliance_soak.summarize([sample])

    failures = appliance_soak.gate_failures(
        summary,
        _args(
            require_recovery=False,
            max_cpu_percent=80.0,
            max_memory_percent=80.0,
            max_model_error_rate=0.1,
            max_model_consecutive_errors=2.0,
            max_vlm_latency_ms=500.0,
        ),
    )

    assert "disk usage 95.0% exceeds 90.0%" in failures
    assert "CPU usage 81.0% exceeds 80.0%" in failures
    assert "memory usage 82.0% exceeds 80.0%" in failures
    assert "model error rate 20.00% exceeds 10.00%" in failures
    assert "model consecutive errors 3 exceeds 2" in failures
    assert "VLM latency 501.0ms exceeds 500.0ms" in failures


def test_post_restart_health_recovery_is_explicit_evidence() -> None:
    before = _sample(ready=False, frame_count=10, dropped_frames=0)
    after = _sample(ready=True, frame_count=20, dropped_frames=0)
    after["post_restart"] = True

    summary = appliance_soak.summarize([before, after])

    assert summary["post_restart_sample_count"] == 1
    assert summary["post_restart_health_recovery_observed"] is True
    assert appliance_soak.gate_failures(summary, _args(require_post_restart_recovery=True)) == [
        "camera readiness pass rate 50.00% is below 100.00%"
    ]

    missing = appliance_soak.summarize([_sample(ready=True, frame_count=10, dropped_frames=0)])
    assert appliance_soak.gate_failures(
        missing,
        _args(
            require_recovery=False,
            require_post_restart_recovery=True,
            min_camera_fps=None,
            max_camera_drop_rate=None,
        ),
    ) == ["no healthy and ready post-restart sample was observed"]


def test_restart_result_is_redacted_and_successful(monkeypatch) -> None:
    class Result:
        returncode = 0

    monkeypatch.setattr(appliance_soak.subprocess, "run", lambda *args, **kwargs: Result())

    result = appliance_soak._run_restart("systemctl restart ksi-cctv-compose", 10.0)

    assert result["executed"] is True
    assert result["program"] == "systemctl"
    assert result["succeeded"] is True
    assert "stdout" not in result
    assert "stderr" not in result
