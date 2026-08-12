#!/usr/bin/env python3
"""Collect evidence for a real KSI-CCTV appliance soak test.

This target-side tool polls authenticated engine health and camera readiness, then
writes a redacted JSON report with deterministic pass/fail gates. It is deliberately
not a camera simulator: operators must run it against actual cameras and hardware.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import ssl
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

JsonFetcher = Callable[[str], tuple[dict[str, Any] | None, str | None]]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    return None


def _camera_label(camera_id: str) -> str:
    """Keep report evidence linkable without exposing customer camera identifiers."""
    return hashlib.sha256(camera_id.encode("utf-8")).hexdigest()[:12]


def _safe_system(
    health: dict[str, Any] | None, system_response: dict[str, Any] | None
) -> dict[str, float | None]:
    system = system_response.get("metrics") if isinstance(system_response, dict) else None
    if not isinstance(system, dict):
        system = health.get("system") if isinstance(health, dict) else None
    system = system if isinstance(system, dict) else {}
    return {
        key: _number(system.get(key))
        for key in (
            "cpu_percent",
            "memory_percent",
            "disk_percent",
            "gpu_percent",
            "gpu_memory_percent",
            "gpu_temperature",
        )
    }


def _safe_ai_metrics(health: dict[str, Any] | None) -> dict[str, float | None]:
    """Keep aggregate health metrics without retaining model names or messages."""
    components = health.get("components") if isinstance(health, dict) else None
    components = components if isinstance(components, list) else []
    metadata_by_name = {
        component.get("name"): component.get("metadata")
        for component in components
        if isinstance(component, dict)
        and isinstance(component.get("name"), str)
        and isinstance(component.get("metadata"), dict)
    }
    vlm = metadata_by_name.get("vlm", {})
    vlm = vlm if isinstance(vlm, dict) else {}
    vlm_latencies = [
        value
        for key in ("avg_latency_ms", "last_latency_ms")
        if (value := _number(vlm.get(key))) is not None and value >= 0
    ]
    model_registry = metadata_by_name.get("model_registry", {})
    models = model_registry.get("models") if isinstance(model_registry, dict) else None
    models = models if isinstance(models, dict) else {}
    model_error_rates: list[float] = []
    consecutive_errors: list[float] = []
    for model in models.values():
        if not isinstance(model, dict):
            continue
        inference_count = _number(model.get("inference_count"))
        error_count = _number(model.get("error_count"))
        if inference_count is not None and error_count is not None and inference_count > 0 and error_count >= 0:
            model_error_rates.append(error_count / inference_count)
        if (value := _number(model.get("consecutive_errors"))) is not None and value >= 0:
            consecutive_errors.append(value)
    return {
        "max_vlm_latency_ms": max(vlm_latencies) if vlm_latencies else None,
        "max_model_error_rate": max(model_error_rates) if model_error_rates else None,
        "max_model_consecutive_errors": max(consecutive_errors) if consecutive_errors else None,
    }


def _safe_readiness(readiness: dict[str, Any] | None, error: str | None) -> dict[str, Any]:
    payload = readiness if isinstance(readiness, dict) else {}
    enabled = payload.get("enabled_cameras")
    healthy = payload.get("healthy_cameras")
    return {
        "ok": bool(payload.get("ready")) and error is None,
        "enabled_count": len(enabled) if isinstance(enabled, list) else 0,
        "healthy_count": len(healthy) if isinstance(healthy, list) else 0,
        "error": error,
    }


def _safe_cameras(camera_health: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    payload = camera_health if isinstance(camera_health, dict) else {}
    raw_cameras = payload.get("cameras")
    if not isinstance(raw_cameras, dict):
        return {}
    output: dict[str, dict[str, Any]] = {}
    for camera_id, raw_status in raw_cameras.items():
        if not isinstance(camera_id, str) or not isinstance(raw_status, dict):
            continue
        output[_camera_label(camera_id)] = {
            "connected": bool(raw_status.get("connected")),
            "stale": bool(raw_status.get("is_stale")),
            "fps": _number(raw_status.get("fps")),
            "frame_count": _number(raw_status.get("frame_count")),
            "dropped_frames": _number(raw_status.get("dropped_frames")),
            "reconnect_attempts": _number(raw_status.get("reconnect_attempts")),
        }
    return output


def collect_sample(fetch: JsonFetcher) -> dict[str, Any]:
    """Fetch only metrics safe to retain in a field validation artifact."""
    health, health_error = fetch("/api/health")
    readiness, readiness_error = fetch("/api/cameras/readiness")
    camera_health, camera_error = fetch("/api/cameras/health")
    system_response, system_error = fetch("/api/system")
    health_status = health.get("status") if isinstance(health, dict) else None
    return {
        "timestamp": _utc_now(),
        "health": {
            "ok": health_status == "healthy" and health_error is None,
            "status": health_status if health_status in {"healthy", "unhealthy"} else "unknown",
            "error": health_error,
        },
        "readiness": _safe_readiness(readiness, readiness_error),
        "system": _safe_system(health, system_response),
        "system_error": system_error,
        "ai": _safe_ai_metrics(health),
        "cameras": _safe_cameras(camera_health),
        "camera_error": camera_error,
    }


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {
            "sample_count": 0,
            "health_pass_rate": 0.0,
            "readiness_pass_rate": 0.0,
            "minimum_enabled_cameras": 0,
            "max_disk_percent": None,
            "max_cpu_percent": None,
            "max_memory_percent": None,
            "max_gpu_temperature_c": None,
            "gpu_metrics_seen": False,
            "minimum_camera_fps": None,
            "camera_drop_rate": None,
            "counter_resets": 0,
            "observed_readiness_recovery": False,
            "max_vlm_latency_ms": None,
            "max_model_error_rate": None,
            "max_model_consecutive_errors": None,
            "model_error_metrics_seen": False,
            "vlm_latency_metrics_seen": False,
            "post_restart_sample_count": 0,
            "post_restart_health_recovery_observed": False,
        }

    health_passes = [bool(sample["health"].get("ok")) for sample in samples]
    readiness_passes = [bool(sample["readiness"].get("ok")) for sample in samples]
    enabled_counts = [int(sample["readiness"].get("enabled_count", 0)) for sample in samples]
    disk_values = [
        value
        for sample in samples
        if (value := _number(sample["system"].get("disk_percent"))) is not None
    ]
    cpu_values = [
        value
        for sample in samples
        if (value := _number(sample["system"].get("cpu_percent"))) is not None
    ]
    memory_values = [
        value
        for sample in samples
        if (value := _number(sample["system"].get("memory_percent"))) is not None
    ]
    gpu_temperatures = [
        value
        for sample in samples
        if (value := _number(sample["system"].get("gpu_temperature"))) is not None
    ]
    fps_values = [
        value
        for sample in samples
        for camera in sample["cameras"].values()
        if (value := _number(camera.get("fps"))) is not None
    ]
    seen_not_ready = False
    observed_recovery = False
    previous_counters: dict[str, tuple[float, float]] = {}
    delta_frames = 0.0
    delta_dropped = 0.0
    counter_resets = 0
    vlm_latencies = [
        value
        for sample in samples
        if (value := _number(sample.get("ai", {}).get("max_vlm_latency_ms"))) is not None
    ]
    model_error_rates = [
        value
        for sample in samples
        if (value := _number(sample.get("ai", {}).get("max_model_error_rate"))) is not None
    ]
    model_consecutive_errors = [
        value
        for sample in samples
        if (value := _number(sample.get("ai", {}).get("max_model_consecutive_errors"))) is not None
    ]
    post_restart_samples = [sample for sample in samples if sample.get("post_restart")]
    for sample in samples:
        ready = bool(sample["readiness"].get("ok"))
        if seen_not_ready and ready:
            observed_recovery = True
        seen_not_ready = seen_not_ready or not ready
        for label, camera in sample["cameras"].items():
            frame_count = _number(camera.get("frame_count"))
            dropped_frames = _number(camera.get("dropped_frames"))
            if frame_count is None or dropped_frames is None:
                continue
            previous = previous_counters.get(label)
            if previous is not None:
                frame_delta = frame_count - previous[0]
                dropped_delta = dropped_frames - previous[1]
                if frame_delta < 0 or dropped_delta < 0:
                    counter_resets += 1
                else:
                    delta_frames += frame_delta
                    delta_dropped += dropped_delta
            previous_counters[label] = (frame_count, dropped_frames)

    return {
        "sample_count": len(samples),
        "health_pass_rate": round(sum(health_passes) / len(samples), 4),
        "readiness_pass_rate": round(sum(readiness_passes) / len(samples), 4),
        "minimum_enabled_cameras": min(enabled_counts),
        "max_disk_percent": max(disk_values) if disk_values else None,
        "max_cpu_percent": max(cpu_values) if cpu_values else None,
        "max_memory_percent": max(memory_values) if memory_values else None,
        "max_gpu_temperature_c": max(gpu_temperatures) if gpu_temperatures else None,
        "gpu_metrics_seen": bool(gpu_temperatures),
        "minimum_camera_fps": min(fps_values) if fps_values else None,
        "camera_drop_rate": round(delta_dropped / delta_frames, 6) if delta_frames else None,
        "counter_resets": counter_resets,
        "observed_readiness_recovery": observed_recovery,
        "max_vlm_latency_ms": max(vlm_latencies) if vlm_latencies else None,
        "max_model_error_rate": max(model_error_rates) if model_error_rates else None,
        "max_model_consecutive_errors": max(model_consecutive_errors) if model_consecutive_errors else None,
        "model_error_metrics_seen": bool(model_error_rates or model_consecutive_errors),
        "vlm_latency_metrics_seen": bool(vlm_latencies),
        "post_restart_sample_count": len(post_restart_samples),
        "post_restart_health_recovery_observed": any(
            sample["health"].get("ok") and sample["readiness"].get("ok")
            for sample in post_restart_samples
        ),
    }


def gate_failures(summary: dict[str, Any], args: argparse.Namespace) -> list[str]:
    failures: list[str] = []
    if summary["sample_count"] == 0:
        return ["no appliance samples collected"]
    if summary["health_pass_rate"] < args.min_health_pass_rate:
        failures.append(
            f"health pass rate {summary['health_pass_rate']:.2%} is below "
            f"{args.min_health_pass_rate:.2%}"
        )
    if summary["readiness_pass_rate"] < args.min_readiness_pass_rate:
        failures.append(
            f"camera readiness pass rate {summary['readiness_pass_rate']:.2%} is below "
            f"{args.min_readiness_pass_rate:.2%}"
        )
    if args.expected_cameras is not None and summary["minimum_enabled_cameras"] < args.expected_cameras:
        failures.append(
            f"enabled cameras dropped to {summary['minimum_enabled_cameras']}; "
            f"expected at least {args.expected_cameras}"
        )
    if summary["max_disk_percent"] is None:
        failures.append("disk usage threshold requested but disk metrics were unavailable")
    elif summary["max_disk_percent"] > args.max_disk_percent:
        failures.append(
            f"disk usage {summary['max_disk_percent']:.1f}% exceeds {args.max_disk_percent:.1f}%"
        )
    for metric, threshold, label in (
        ("max_cpu_percent", args.max_cpu_percent, "CPU usage"),
        ("max_memory_percent", args.max_memory_percent, "memory usage"),
    ):
        if threshold is None:
            continue
        value = summary[metric]
        if value is None:
            failures.append(f"{label} threshold requested but metrics were unavailable")
        elif value > threshold:
            failures.append(f"{label} {value:.1f}% exceeds {threshold:.1f}%")
    if args.require_cpu_metrics and summary["max_cpu_percent"] is None:
        failures.append("CPU metrics were required but unavailable")
    if args.require_memory_metrics and summary["max_memory_percent"] is None:
        failures.append("memory metrics were required but unavailable")
    if summary["max_gpu_temperature_c"] is not None and summary["max_gpu_temperature_c"] > args.max_gpu_temperature_c:
        failures.append(
            f"GPU temperature {summary['max_gpu_temperature_c']:.1f}C exceeds "
            f"{args.max_gpu_temperature_c:.1f}C"
        )
    if args.require_gpu_metrics and not summary["gpu_metrics_seen"]:
        failures.append("GPU metrics were required but unavailable")
    if args.min_camera_fps is not None:
        fps = summary["minimum_camera_fps"]
        if fps is None:
            failures.append("camera FPS threshold requested but no FPS metrics were available")
        elif fps < args.min_camera_fps:
            failures.append(f"camera FPS {fps:.2f} is below {args.min_camera_fps:.2f}")
    if args.max_camera_drop_rate is not None:
        drop_rate = summary["camera_drop_rate"]
        if drop_rate is None:
            failures.append("camera drop-rate threshold requested but frame counters were unavailable")
        elif drop_rate > args.max_camera_drop_rate:
            failures.append(
                f"camera drop rate {drop_rate:.2%} exceeds {args.max_camera_drop_rate:.2%}"
            )
    if args.require_recovery and not summary["observed_readiness_recovery"]:
        failures.append("no not-ready to ready recovery was observed")
    if args.max_model_error_rate is not None:
        error_rate = summary["max_model_error_rate"]
        if error_rate is None:
            failures.append("model error-rate threshold requested but model metrics were unavailable")
        elif error_rate > args.max_model_error_rate:
            failures.append(f"model error rate {error_rate:.2%} exceeds {args.max_model_error_rate:.2%}")
    if args.max_model_consecutive_errors is not None:
        errors = summary["max_model_consecutive_errors"]
        if errors is None:
            failures.append("model consecutive-error threshold requested but model metrics were unavailable")
        elif errors > args.max_model_consecutive_errors:
            failures.append(f"model consecutive errors {errors:.0f} exceeds {args.max_model_consecutive_errors:.0f}")
    if args.max_vlm_latency_ms is not None:
        latency = summary["max_vlm_latency_ms"]
        if latency is None:
            failures.append("VLM latency threshold requested but VLM metrics were unavailable")
        elif latency > args.max_vlm_latency_ms:
            failures.append(f"VLM latency {latency:.1f}ms exceeds {args.max_vlm_latency_ms:.1f}ms")
    if args.require_post_restart_recovery and not summary["post_restart_health_recovery_observed"]:
        failures.append("no healthy and ready post-restart sample was observed")
    return failures


def _validate_engine_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise argparse.ArgumentTypeError("--engine-url must be an http(s) URL with a host")
    if parsed.username or parsed.password:
        raise argparse.ArgumentTypeError("--engine-url must not contain credentials")
    return value.rstrip("/")


def _ssl_context(args: argparse.Namespace) -> ssl.SSLContext | None:
    if not args.engine_url.startswith("https://"):
        return None
    if args.insecure_tls:
        host = urlsplit(args.engine_url).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("--insecure-tls is only allowed for a loopback engine URL")
        return ssl._create_unverified_context()
    return ssl.create_default_context(cafile=args.ca_file)


def _make_fetcher(
    base_url: str,
    api_key: str,
    timeout: float,
    context: ssl.SSLContext | None,
) -> JsonFetcher:
    def fetch(path: str) -> tuple[dict[str, Any] | None, str | None]:
        request = Request(
            f"{base_url}{path}",
            headers={"Accept": "application/json", "X-API-Key": api_key},
        )
        try:
            with urlopen(request, timeout=timeout, context=context) as response:
                data = json.loads(response.read().decode("utf-8"))
            return (data, None) if isinstance(data, dict) else (None, "invalid_json_payload")
        except HTTPError as exc:
            return None, f"http_{exc.code}"
        except URLError as exc:
            return None, type(exc.reason).__name__
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return None, type(exc).__name__

    return fetch


def _run_restart(command: str, timeout: float) -> dict[str, Any]:
    parts = shlex.split(command)
    if not parts:
        return {"executed": False, "error": "empty_command"}
    try:
        result = subprocess.run(parts, check=False, capture_output=True, text=True, timeout=timeout)
        return {
            "executed": True,
            "executed_at": _utc_now(),
            "program": Path(parts[0]).name,
            "returncode": result.returncode,
            "succeeded": result.returncode == 0,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "executed": True,
            "executed_at": _utc_now(),
            "program": Path(parts[0]).name,
            "error": type(exc).__name__,
            "succeeded": False,
        }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect field-appliance soak evidence without source URLs")
    parser.add_argument("--engine-url", required=True, type=_validate_engine_url)
    parser.add_argument("--api-key-env", default="KSI_CCTV_API_KEY")
    parser.add_argument("--ca-file", help="CA PEM used to verify the Appliance HTTPS certificate")
    parser.add_argument("--insecure-tls", action="store_true", help="loopback HTTPS only; do not use on LAN")
    parser.add_argument("--duration", type=float, default=1800.0, help="soak duration in seconds")
    parser.add_argument("--interval", type=float, default=30.0, help="poll interval in seconds")
    parser.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    parser.add_argument("--expected-cameras", type=int)
    parser.add_argument("--min-health-pass-rate", type=float, default=1.0)
    parser.add_argument("--min-readiness-pass-rate", type=float, default=1.0)
    parser.add_argument("--max-disk-percent", type=float, default=90.0)
    parser.add_argument("--max-cpu-percent", type=float)
    parser.add_argument("--max-memory-percent", type=float)
    parser.add_argument("--require-cpu-metrics", action="store_true")
    parser.add_argument("--require-memory-metrics", action="store_true")
    parser.add_argument("--max-gpu-temperature-c", type=float, default=85.0)
    parser.add_argument("--require-gpu-metrics", action="store_true")
    parser.add_argument("--min-camera-fps", type=float)
    parser.add_argument("--max-camera-drop-rate", type=float)
    parser.add_argument("--require-recovery", action="store_true")
    parser.add_argument("--max-model-error-rate", type=float)
    parser.add_argument("--max-model-consecutive-errors", type=float)
    parser.add_argument("--max-vlm-latency-ms", type=float)
    parser.add_argument("--restart-command", help="operator-approved local command; never executed by default")
    parser.add_argument("--restart-after-sec", type=float, help="when to execute --restart-command")
    parser.add_argument("--restart-timeout", type=float, default=120.0)
    parser.add_argument("--allow-restart", action="store_true", help="required to execute --restart-command")
    parser.add_argument("--require-post-restart-recovery", action="store_true")
    parser.add_argument("--json-out", required=True, help="evidence report path")
    args = parser.parse_args(argv)
    if args.duration <= 0 or args.interval <= 0 or args.timeout <= 0 or args.restart_timeout <= 0:
        parser.error("duration, interval, timeout, and restart-timeout must be positive")
    if args.expected_cameras is not None and args.expected_cameras < 1:
        parser.error("--expected-cameras must be at least 1")
    for name in ("min_health_pass_rate", "min_readiness_pass_rate", "max_camera_drop_rate", "max_model_error_rate"):
        value = getattr(args, name)
        if value is not None and not 0 <= value <= 1:
            parser.error(f"--{name.replace('_', '-')} must be between 0 and 1")
    if any(
        value is not None and value < 0
        for value in (
            args.max_disk_percent,
            args.max_cpu_percent,
            args.max_memory_percent,
            args.max_gpu_temperature_c,
            args.max_model_consecutive_errors,
            args.max_vlm_latency_ms,
        )
    ):
        parser.error("resource thresholds must be non-negative")
    if args.min_camera_fps is not None and args.min_camera_fps < 0:
        parser.error("--min-camera-fps must be non-negative")
    if args.restart_command and not args.allow_restart:
        parser.error("--restart-command requires --allow-restart")
    if args.allow_restart and not args.restart_command:
        parser.error("--allow-restart requires --restart-command")
    if args.restart_after_sec is not None:
        if not args.restart_command:
            parser.error("--restart-after-sec requires --restart-command")
        if not 0 <= args.restart_after_sec < args.duration:
            parser.error("--restart-after-sec must be within the soak duration")
    if args.require_post_restart_recovery and not args.restart_command:
        parser.error("--require-post-restart-recovery requires --restart-command")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key:
        print(f"ERROR: API key environment variable {args.api_key_env} is not set", file=sys.stderr)
        return 2
    try:
        context = _ssl_context(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    fetch = _make_fetcher(args.engine_url, api_key, args.timeout, context)
    started_at = _utc_now()
    started = time.monotonic()
    deadline = started + args.duration
    samples: list[dict[str, Any]] = []
    restart: dict[str, Any] | None = None
    while True:
        now = time.monotonic()
        if args.restart_command and restart is None and now - started >= (args.restart_after_sec or 0):
            restart = _run_restart(args.restart_command, args.restart_timeout)
        sample = collect_sample(fetch)
        sample["post_restart"] = restart is not None
        samples.append(sample)
        print(
            f"{sample['timestamp']} health={sample['health']['status']} "
            f"ready={sample['readiness']['ok']} cameras={sample['readiness']['healthy_count']}/"
            f"{sample['readiness']['enabled_count']}"
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.interval, remaining))

    summary = summarize(samples)
    failures = gate_failures(summary, args)
    if restart is not None and not restart.get("succeeded"):
        failures.append("restart command did not complete successfully")
    report = {
        "schema_version": 1,
        "started_at": started_at,
        "ended_at": _utc_now(),
        "configuration": {
            "duration_sec": args.duration,
            "interval_sec": args.interval,
            "expected_cameras": args.expected_cameras,
            "restart_requested": bool(args.restart_command),
            "require_post_restart_recovery": args.require_post_restart_recovery,
        },
        "samples": samples,
        "restart": restart,
        "summary": {**summary, "ok": not failures, "gate_failures": failures},
    }
    output = Path(args.json_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
