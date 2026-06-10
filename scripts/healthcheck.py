#!/usr/bin/env python3
"""KSI-CCTV deployment healthcheck.

Checks local deployment readiness without printing secrets or raw RTSP URLs.
By default, CUDA absence is a warning because DGX Spark/ARM64 provider support
may vary and the app must retain CPU fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class CheckResult:
    name: str
    status: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class Healthcheck:
    def __init__(self) -> None:
        self.results: list[CheckResult] = []

    def pass_(self, name: str, message: str, **details: Any) -> None:
        self.results.append(CheckResult(name, "pass", message, _safe_details(details)))

    def warn(self, name: str, message: str, **details: Any) -> None:
        self.results.append(CheckResult(name, "warn", message, _safe_details(details)))

    def fail(self, name: str, message: str, **details: Any) -> None:
        self.results.append(CheckResult(name, "fail", message, _safe_details(details)))

    @property
    def ok(self) -> bool:
        return not any(item.status == "fail" for item in self.results)


def _safe_details(details: dict[str, Any]) -> dict[str, Any]:
    return {key: _redact_value(key, value) for key, value in details.items()}


def _redact_value(key: str, value: Any) -> Any:
    key_l = key.lower()
    if any(token in key_l for token in ("password", "secret", "token", "credential")):
        return "***"
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        return {str(k): _redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    return value


def _redact_text(text: str) -> str:
    """Mask URLs and common secret assignments in arbitrary status/error text."""
    redacted = re.sub(
        r"\b(rtsp|https?)://[^\s'\"<>]+",
        lambda match: _mask_url(match.group(0)),
        text,
        flags=re.IGNORECASE,
    )
    redacted = re.sub(
        r"(?i)\b([A-Z0-9_]*(PASSWORD|SECRET|TOKEN|CREDENTIAL)[A-Z0-9_]*\s*=\s*)[^\s,;]+",
        r"\1***",
        redacted,
    )
    return redacted


def _mask_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme in {"rtsp", "http", "https"}:
        host = parsed.hostname or "host"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://***@{host}{port}/***"
    return "***"


def _source_type(url: str) -> str:
    if not url:
        return "empty"
    if "${" in url or url.startswith("$"):
        return "unresolved_env"
    parsed = urlparse(url)
    if parsed.scheme in {"rtsp", "http", "https"}:
        return parsed.scheme
    return "file" if Path(url).suffix else "unknown"


def _check_imports(hc: Healthcheck) -> None:
    modules = ["cv2", "numpy", "onnxruntime", "yaml", "streamlit"]
    missing: list[str] = []
    for module in modules:
        try:
            __import__(module)
        except Exception:
            missing.append(module)
    if missing:
        hc.fail("python_imports", "Required Python modules are missing", missing=missing)
    else:
        hc.pass_("python_imports", "Required Python modules import successfully", modules=modules)


def _check_config(hc: Healthcheck, config_path: Path, cameras_path: Path, model_profiles_path: Path) -> tuple[Any, list[dict[str, Any]]]:
    from src.utils.config import AppConfig, ConfigLoader

    config = None
    cameras: list[dict[str, Any]] = []
    for name, path in (
        ("default_config", config_path),
        ("cameras_config", cameras_path),
        ("model_profiles_config", model_profiles_path),
    ):
        if not path.exists():
            hc.fail(name, "Required config file is missing", path=str(path))
            continue
        try:
            ConfigLoader.load(str(path))
            hc.pass_(name, "Config file parses", path=str(path))
        except Exception as exc:
            hc.fail(name, "Config file failed to parse", path=str(path), error=str(exc))

    try:
        config = AppConfig(str(config_path))
        cameras = config.load_cameras(str(cameras_path))
    except Exception as exc:
        hc.fail("app_config", "AppConfig failed to load", error=str(exc))
    return config, cameras


def _check_runtime_dirs(hc: Healthcheck, paths: list[Path], create_dirs: bool = False) -> None:
    for path in paths:
        try:
            if not path.exists():
                if create_dirs:
                    path.mkdir(parents=True, exist_ok=True)
                    hc.pass_("runtime_dir", "Runtime directory was created", path=str(path))
                else:
                    hc.fail("runtime_dir", "Runtime directory is missing (use --create-dirs to create it)", path=str(path))
                    continue
            if not path.is_dir():
                hc.fail("runtime_dir", "Runtime path exists but is not a directory", path=str(path))
                continue
            if os.access(path, os.W_OK | os.X_OK):
                hc.pass_("runtime_dir", "Runtime directory exists and appears writable", path=str(path))
            else:
                hc.fail("runtime_dir", "Runtime directory is not writable", path=str(path))
        except Exception as exc:
            hc.fail("runtime_dir", "Runtime directory is not writable", path=str(path), error=str(exc))


def _check_providers_and_model(hc: Healthcheck, config: Any, require_gpu: bool) -> None:
    try:
        import onnxruntime as ort

        available = list(ort.get_available_providers())
        has_cuda = "CUDAExecutionProvider" in available
        if has_cuda:
            hc.pass_("onnxruntime_providers", "CUDA provider is available", providers=available)
        elif require_gpu:
            hc.fail("onnxruntime_providers", "CUDA provider is required but unavailable", providers=available)
        else:
            hc.warn("onnxruntime_providers", "CUDA provider unavailable; CPU fallback will be used", providers=available)
    except Exception as exc:
        hc.fail("onnxruntime_providers", "Failed to inspect ONNX Runtime providers", error=str(exc))
        return

    if config is None:
        return

    try:
        from src.detection.yolo_detector import YOLODetector

        yolo_cfg = config.get("models", "yolo", default={})
        device = config.get("device", "primary", default="cuda")
        detector = YOLODetector(
            model_path=yolo_cfg.get("model_path", "./models/yolo26.onnx"),
            input_size=tuple(yolo_cfg.get("input_size", [640, 640])),
            conf_threshold=float(yolo_cfg.get("conf_threshold", 0.25)),
            iou_threshold=float(yolo_cfg.get("iou_threshold", 0.45)),
            postprocess_mode=yolo_cfg.get("postprocess_mode", "auto"),
            class_profile=yolo_cfg.get("class_profile", "coco"),
            classes=yolo_cfg.get("classes") or None,
            device=device,
            fp16=bool(config.get("device", "fp16", default=True)),
        )
        used = detector.session.get_providers() if detector.session else []
        if "CUDAExecutionProvider" in used:
            hc.pass_("yolo_model", "YOLO model loaded with CUDA provider", providers=used)
        elif require_gpu:
            hc.fail("yolo_model", "YOLO model loaded without required CUDA provider", providers=used)
        else:
            hc.pass_("yolo_model", "YOLO model loaded with CPU fallback", providers=used)
    except Exception as exc:
        hc.fail("yolo_model", "Failed to load YOLO model", error=str(exc))


def _check_retention(hc: Healthcheck, config: Any) -> None:
    if config is None:
        return
    alerts = config.get_alert_config()
    retention = int(alerts.get("image_retention_days", 0))
    max_bytes = int(alerts.get("log_max_bytes", 0))
    backup_count = int(alerts.get("log_backup_count", 0))
    if retention > 0 and max_bytes > 0 and backup_count > 0:
        hc.pass_(
            "retention_policy",
            "Log/capture retention settings are configured",
            image_retention_days=retention,
            log_max_bytes=max_bytes,
            log_backup_count=backup_count,
        )
    else:
        hc.warn(
            "retention_policy",
            "Retention settings should be positive for production",
            image_retention_days=retention,
            log_max_bytes=max_bytes,
            log_backup_count=backup_count,
        )


def _check_cameras(hc: Healthcheck, cameras: list[dict[str, Any]], check_cameras: bool) -> None:
    enabled = [camera for camera in cameras if camera.get("enabled")]
    if not enabled:
        hc.warn("cameras", "No enabled cameras configured")
        return

    for camera in enabled:
        camera_id = camera.get("id", "unknown")
        stream_url = str(camera.get("stream_url", ""))
        stype = _source_type(stream_url)
        if stype in {"empty", "unresolved_env"}:
            hc.warn("camera_config", "Enabled camera has missing or unresolved stream URL", camera_id=camera_id, source_type=stype)
            continue
        if stype == "file":
            exists = Path(stream_url).exists()
            if exists:
                hc.pass_("camera_config", "Enabled camera file source exists", camera_id=camera_id, source_type=stype)
            else:
                hc.warn("camera_config", "Enabled camera file source is missing", camera_id=camera_id, source_type=stype)
        else:
            hc.pass_("camera_config", "Enabled camera URL is resolved", camera_id=camera_id, source_type=stype)

        if check_cameras:
            _probe_camera(hc, camera_id, stream_url)


def _probe_camera(hc: Healthcheck, camera_id: str, stream_url: str) -> None:
    try:
        import cv2

        cap = cv2.VideoCapture(stream_url)
        try:
            start = time.time()
            ok, _ = cap.read()
            elapsed_ms = round((time.time() - start) * 1000, 1)
            if ok:
                hc.pass_("camera_probe", "Camera/video source returned a frame", camera_id=camera_id, elapsed_ms=elapsed_ms)
            else:
                hc.warn("camera_probe", "Camera/video source did not return a frame", camera_id=camera_id, elapsed_ms=elapsed_ms)
        finally:
            cap.release()
    except Exception as exc:
        hc.warn("camera_probe", "Camera probe failed", camera_id=camera_id, error=str(exc))


def _check_vlm(hc: Healthcheck, config: Any) -> None:
    if config is None:
        return
    try:
        from src.vlm.ollama_provider import OllamaProvider

        vlm_cfg = config.get("models", "vlm", default={})
        if not bool(vlm_cfg.get("enabled", False)):
            hc.warn("vlm", "VLM is disabled in config")
            return
        provider = OllamaProvider(
            base_url=vlm_cfg.get("base_url", "http://localhost:11434"),
            model=vlm_cfg.get("model_name", "qwen3-vl:latest"),
            timeout_sec=5,
            max_retries=0,
        )
        if provider.is_available():
            hc.pass_("vlm", "Configured Ollama VLM is available", model=vlm_cfg.get("model_name", "qwen3-vl:latest"))
        else:
            hc.warn("vlm", "Configured Ollama VLM is not available", model=vlm_cfg.get("model_name", "qwen3-vl:latest"))
    except Exception as exc:
        hc.warn("vlm", "VLM availability check failed", error=str(exc))


def _check_dashboard_port(hc: Healthcheck, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        result = sock.connect_ex(("127.0.0.1", port))
    if result == 0:
        hc.warn("dashboard_port", "Dashboard port is already in use", port=port)
    else:
        hc.pass_("dashboard_port", "Dashboard port appears available", port=port)


def _print_plain(results: list[CheckResult]) -> None:
    labels = {"pass": "PASS", "warn": "WARN", "fail": "FAIL"}
    for result in results:
        print(f"{labels.get(result.status, result.status.upper())} {result.name}: {result.message}")
        if result.details:
            print(f"  {json.dumps(result.details, ensure_ascii=False, sort_keys=True)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="KSI-CCTV deployment healthcheck")
    parser.add_argument("--config", default="./config/default.yaml")
    parser.add_argument("--cameras", default="./config/cameras.json")
    parser.add_argument("--model-profiles", default="./config/model_profiles.yaml")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    parser.add_argument("--require-gpu", action="store_true", help="Fail if CUDA provider is unavailable or unused")
    parser.add_argument("--check-vlm", action="store_true", help="Check configured Ollama VLM availability without inference")
    parser.add_argument("--check-cameras", action="store_true", help="Attempt to open enabled camera/video sources")
    parser.add_argument(
        "--create-dirs",
        "--repair-dirs",
        dest="create_dirs",
        action="store_true",
        help="Create missing runtime directories before checking them. Default is read-only.",
    )
    args = parser.parse_args()

    os.chdir(ROOT)

    hc = Healthcheck()
    try:
        from src.utils.config import ConfigLoader

        ConfigLoader.load_env(args.env)
        hc.pass_("environment", "Environment file loaded if present", env_file=args.env, present=Path(args.env).exists())
    except Exception as exc:
        hc.fail("environment", "Failed to load environment file", error=str(exc))

    _check_imports(hc)
    config, cameras = _check_config(hc, Path(args.config), Path(args.cameras), Path(args.model_profiles))
    _check_runtime_dirs(hc, [Path("logs"), Path("data/captures"), Path("models/uploaded")], create_dirs=args.create_dirs)
    _check_providers_and_model(hc, config, args.require_gpu)
    _check_retention(hc, config)
    _check_cameras(hc, cameras, args.check_cameras)
    if config is not None:
        ui = config.get_ui_config()
        _check_dashboard_port(hc, int(ui.get("dashboard_port", 8080)))
    if args.check_vlm:
        _check_vlm(hc, config)

    summary = {
        "ok": hc.ok,
        "passes": sum(1 for item in hc.results if item.status == "pass"),
        "warnings": sum(1 for item in hc.results if item.status == "warn"),
        "failures": sum(1 for item in hc.results if item.status == "fail"),
        "results": [asdict(item) for item in hc.results],
    }
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_plain(hc.results)
        print(
            f"Summary: {summary['passes']} passed, {summary['warnings']} warnings, {summary['failures']} failures"
        )
    return 0 if hc.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
