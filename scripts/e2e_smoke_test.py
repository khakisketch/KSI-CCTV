"""Non-interactive E2E smoke validation for KSI-CCTV.

This script validates the core local path without starting Streamlit:
camera/video source -> frame read -> YOLO ONNX inference -> postprocess -> optional VLM.
It intentionally does not print raw RTSP URLs to avoid leaking credentials.

Phase 4+: supports both legacy single-detector mode and policy-based pipeline mode.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detection.yolo_detector import YOLODetector  # noqa: E402
from src.utils.config import AppConfig, ConfigLoader  # noqa: E402
from src.vlm.context_builder import ContextBuilder  # noqa: E402
from src.vlm.ollama_provider import OllamaProvider  # noqa: E402


def _run_policy_mode(
    frame: np.ndarray,
    model_profiles_path: str = "./config/model_profiles.yaml",
    event_policies_path: str = "./config/event_policies.yaml",
) -> dict[str, Any]:
    """Phase 4 정책 기반 파이프라인 smoke test."""
    from src.detection.model_registry import MultiModelRegistry  # noqa: E402
    from src.policy.event_policy_engine import EventPolicyEngine  # noqa: E402

    registry = MultiModelRegistry.from_config(model_profiles_path)
    engine = EventPolicyEngine.from_config(event_policies_path)

    all_detections = registry.detect_all(frame)
    decisions = engine.evaluate(
        profile_name="construction",
        model_detections=all_detections,
        frame=frame,
        camera_id="cam_001",
    )

    return {
        "ok": True,
        "active_models": registry.active_model_names,
        "failed_models": dict(registry.failed_models),
        "detections_by_model": {
            name: [d.to_dict() for d in det_list[:20]]
            for name, det_list in all_detections.items()
        },
        "total_detections": sum(len(v) for v in all_detections.values()),
        "policy_decisions": [
            {
                "event_type": d.event.event_type,
                "alert_level": getattr(d.event, "alert_level", ""),
                "vlm_needed": getattr(d.event, "vlm_needed", False),
                "source_models": list(getattr(d.event, "source_models", [])),
                "rule_name": getattr(d.event, "rule_name", ""),
            }
            for d in decisions
        ],
    }


def _first_enabled_camera(cameras: list[dict[str, Any]], camera_id: str | None) -> dict[str, Any]:
    enabled = [camera for camera in cameras if camera.get("enabled", True)]
    if camera_id:
        for camera in enabled:
            if camera.get("id") == camera_id:
                return camera
        raise ValueError(f"Enabled camera not found: {camera_id}")
    if not enabled:
        raise ValueError("No enabled camera found")
    return enabled[0]


def _run_legacy_mode(
    detector: YOLODetector, frame: np.ndarray
) -> tuple[list, list[dict[str, Any]]]:
    input_tensor = detector.preprocess(frame)
    outputs = detector.session.run(detector.output_names, {detector.input_name: input_tensor})
    detector._log_output_shapes_once(outputs)
    detections = detector._postprocess(outputs, frame.shape[:2])
    output_meta = [
        {"shape": list(np.asarray(output).shape), "dtype": str(np.asarray(output).dtype)}
        for output in outputs
    ]
    return detections, output_meta


def _read_frame(stream_url: str, max_frames: int) -> np.ndarray:
    cap = cv2.VideoCapture(stream_url)
    if not cap.isOpened():
        raise RuntimeError("Failed to open configured camera/video source")
    try:
        frame = None
        for _ in range(max(1, max_frames)):
            ok, candidate = cap.read()
            if not ok:
                break
            frame = candidate
        if frame is None:
            raise RuntimeError("Failed to read a frame from configured source")
        return frame
    finally:
        cap.release()


def _maybe_run_vlm(config: AppConfig, frame: np.ndarray, detections: list) -> dict[str, Any]:
    vlm_cfg = config.get("models", "vlm", default={})
    provider = OllamaProvider(
        base_url=vlm_cfg.get("base_url", "http://localhost:11434"),
        model=vlm_cfg.get("model_name", "qwen3-vl:latest"),
        timeout_sec=float(vlm_cfg.get("timeout_sec", 60)),
        max_retries=int(vlm_cfg.get("max_retries", 2)),
    )
    available = provider.is_available()
    result: dict[str, Any] = {"available": available}
    if not available:
        return result

    ok, buffer = cv2.imencode(".jpg", frame)
    if not ok:
        raise RuntimeError("Failed to encode frame for VLM")
    builder = ContextBuilder(vlm_cfg.get("prompt_template", "./config/prompts/construction.yaml"))
    prompt = builder.build(
        detections_summary=ContextBuilder.format_detections(detections),
        recent_events="최근 이벤트 없음",
    )
    analysis = provider.analyze(buffer.tobytes(), prompt)
    result.update(
        {
            "description": analysis.description,
            "risk_level": analysis.risk_level,
            "confidence": analysis.confidence,
            "inference_time_ms": analysis.inference_time_ms,
            "detected_events": analysis.detected_events,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a KSI-CCTV non-interactive E2E smoke test")
    parser.add_argument("--config", default="./config/default.yaml", help="App config path")
    parser.add_argument("--cameras", default="./config/cameras.json", help="Camera config path")
    parser.add_argument("--env", default=".env", help="Optional env file path")
    parser.add_argument("--camera-id", default=None, help="Enabled camera id to test")
    parser.add_argument("--max-frames", type=int, default=3, help="Frames to advance before testing")
    parser.add_argument("--with-vlm", action="store_true", help="Also call the configured Ollama VLM")
    parser.add_argument("--policy-mode", action="store_true", help="Use MultiModelRegistry + EventPolicyEngine (Phase 4)")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    start = time.time()
    ConfigLoader.load_env(args.env)

    if args.policy_mode:
        # Phase 4 정책 기반 모드
        cameras = []
        stream_url = "./data/sample.mp4"
        if not Path(stream_url).exists():
            raise RuntimeError(f"Sample video not found for policy mode: {stream_url}")

        frame = _read_frame(stream_url, args.max_frames)
        result = _run_policy_mode(frame)
        result["source_type"] = "file"
        result["frame_shape"] = list(frame.shape)
        result["elapsed_ms"] = round((time.time() - start) * 1000, 1)

        if args.with_vlm:
            # VLM도 함께 실행 (기존 방식과 동일)
            yolo_cfg = AppConfig(args.config).get("models", "yolo", default={})
            device = AppConfig(args.config).get("device", "primary", default="cuda")
            detector = YOLODetector(
                model_path=yolo_cfg.get("model_path", "./models/yolo26.onnx"),
                input_size=tuple(yolo_cfg.get("input_size", [640, 640])),
                conf_threshold=float(yolo_cfg.get("conf_threshold", 0.25)),
                iou_threshold=float(yolo_cfg.get("iou_threshold", 0.45)),
                postprocess_mode=yolo_cfg.get("postprocess_mode", "auto"),
                class_profile=yolo_cfg.get("class_profile", "coco"),
                device=device,
            )
            detections, _ = _run_legacy_mode(detector, frame)
            result["vlm"] = _maybe_run_vlm(AppConfig(args.config), frame, detections)

        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    # Legacy 단일 detector 모드
    config = AppConfig(args.config)
    cameras = config.load_cameras(args.cameras)
    camera = _first_enabled_camera(cameras, args.camera_id)
    stream_url = camera.get("stream_url", "")
    if not stream_url or "$" in stream_url:
        raise RuntimeError("Camera stream_url is empty or unresolved. Check .env and config/cameras.json")

    frame = _read_frame(stream_url, args.max_frames)

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
    detections, output_meta = _run_legacy_mode(detector, frame)

    safety_check = detector.detect_safety_gear(detections)
    summary: dict[str, Any] = {
        "ok": True,
        "camera_id": camera.get("id"),
        "source_type": "file" if Path(stream_url).suffix.lower() else "stream",
        "frame_shape": list(frame.shape),
        "providers": detector.session.get_providers(),
        "input_name": detector.input_name,
        "input_dtype": str(detector.input_dtype),
        "output_meta": output_meta,
        "detections": [d.to_dict() for d in detections[:20]],
        "detection_count": len(detections),
        "safety_violations": len(safety_check.get("violations", [])),
        "elapsed_ms": round((time.time() - start) * 1000, 1),
    }

    if args.with_vlm:
        summary["vlm"] = _maybe_run_vlm(config, frame, detections)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        raise SystemExit(1) from exc
