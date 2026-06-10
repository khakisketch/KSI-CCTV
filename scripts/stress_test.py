#!/usr/bin/env python3
"""비디오 파일 기반 통합 테스트 (Phase 9 M3).

웹캠이 없는 환경에서 sample.mp4 또는 사용자 지정 비디오 파일을 반복 재생하여
전체 파이프라인(다중 모델 추론 + 이벤트 정책 + VLM)을 장시간 안정적으로 테스트한다.

주요 기능:
- 단일 비디오 파일 반복 재생 (RTSP 카메라 시뮬레이션)
- 멀티 카메라 스트레스 테스트 (동일 비디오를 N개 카메라로 시뮬레이션)
- 프레임 처리 시간 / 추론 레이턴시 측정
- 이벤트 카운트 / 알람 발송 카운트
- 실행 시간/프레임 수 기반 통계 출력

사용 예:
    python scripts/stress_test.py --video ./data/sample.mp4 --duration 60
    python scripts/stress_test.py --video ./data/sample.mp4 --duration 300 --cameras 2
    python scripts/stress_test.py --video ./data/sample.mp4 --policy-mode --with-vlm
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_video(video_path: str, loop: bool = True) -> Optional[cv2.VideoCapture]:
    """비디오 파일을 로드. loop=True면 끝까지 가면 처음부터 다시 재생."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    return cap


def _read_frames(cap: cv2.VideoCapture, max_frames: int = 0, loop: bool = True) -> List[np.ndarray]:
    """비디오에서 프레임들을 읽음. loop=True면 영상이 끝나면 처음으로 돌아감."""
    frames = []
    count = 0
    while max_frames == 0 or count < max_frames:
        ret, frame = cap.read()
        if not ret:
            if loop:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            break
        frames.append(frame)
        count += 1
    return frames


def _create_pipeline(args: argparse.Namespace) -> Dict[str, Any]:
    """파이프라인 컴포넌트 생성 (1회만 호출)."""
    components: Dict[str, Any] = {"policy_mode": args.policy_mode}
    if args.policy_mode:
        from src.detection.model_registry import MultiModelRegistry
        from src.policy.event_policy_engine import EventPolicyEngine

        components["registry"] = MultiModelRegistry.from_config(args.model_profiles)
        components["engine"] = EventPolicyEngine.from_config(args.event_policies)
    else:
        from src.detection.yolo_detector import YOLODetector
        from src.utils.config import AppConfig

        config = AppConfig(args.config)
        yolo_cfg = config.get("models", "yolo", default={})
        device = config.get("device", "primary", default="cuda")
        components["detector"] = YOLODetector(
            model_path=yolo_cfg.get("model_path", "./models/yolo26.onnx"),
            input_size=tuple(yolo_cfg.get("input_size", [640, 640])),
            conf_threshold=float(yolo_cfg.get("conf_threshold", 0.25)),
            iou_threshold=float(yolo_cfg.get("iou_threshold", 0.45)),
            postprocess_mode=yolo_cfg.get("postprocess_mode", "auto"),
            class_profile=yolo_cfg.get("class_profile", "coco"),
            device=device,
        )
    return components


def _run_single_frame_pipeline(
    frame: np.ndarray,
    args: argparse.Namespace,
    components: Dict[str, Any],
    cam_id: str = "stress_test_cam",
) -> Dict[str, Any]:
    """단일 프레임에 대해 파이프라인 실행 (재사용된 컴포넌트 사용)."""
    result: Dict[str, Any] = {
        "frame_shape": list(frame.shape),
        "inference_ms": 0.0,
        "total_ms": 0.0,
    }
    t0 = time.time()

    if args.policy_mode:
        registry = components["registry"]
        engine = components["engine"]

        t_inf0 = time.time()
        all_detections = registry.detect_all(frame)
        result["inference_ms"] = round((time.time() - t_inf0) * 1000, 1)

        decisions = engine.evaluate(
            profile_name="construction",
            model_detections=all_detections,
            frame=frame,
            camera_id=cam_id,
        )
        result["active_models"] = list(registry.active_model_names)
        result["failed_models"] = list(registry.failed_models.keys())
        result["total_detections"] = sum(len(d) for d in all_detections.values())
        result["camera_id"] = cam_id
        result["policy_decisions"] = [
            {
                "event_type": d.event.event_type,
                "alert_level": d.alert_level,
                "policy_name": d.policy_name,
                "source_models": d.source_models,
            }
            for d in decisions
        ]
    else:
        detector = components["detector"]
        t_inf0 = time.time()
        detections = detector.detect(frame)
        result["inference_ms"] = round((time.time() - t_inf0) * 1000, 1)
        result["detection_count"] = len(detections)
        result["camera_id"] = cam_id

    result["total_ms"] = round((time.time() - t0) * 1000, 1)
    return result


def _maybe_run_vlm(frame: np.ndarray) -> Optional[Dict[str, Any]]:
    """VLM 호출 (선택). Ollama가 사용 가능할 때만 실행."""
    try:
        from src.utils.config import AppConfig
        from src.vlm.ollama_provider import OllamaProvider

        config = AppConfig("./config/default.yaml")
        vlm_cfg = config.get("vlm", default={}) if hasattr(config, "get") else {}
        if not vlm_cfg:
            vlm_cfg = {
                "base_url": "http://localhost:11434",
                "model_name": "qwen3-vl:latest",
                "timeout_sec": 60,
                "max_retries": 2,
                "prompt_template": "./config/prompts/construction.yaml",
            }
        provider = OllamaProvider(
            base_url=vlm_cfg.get("base_url", "http://localhost:11434"),
            model=vlm_cfg.get("model_name", "qwen3-vl:latest"),
            timeout_sec=float(vlm_cfg.get("timeout_sec", 60)),
            max_retries=int(vlm_cfg.get("max_retries", 2)),
        )
        if not provider.is_available():
            return {"available": False}
        _, buf = cv2.imencode(".jpg", frame)
        from src.vlm.context_builder import ContextBuilder
        builder = ContextBuilder(vlm_cfg.get("prompt_template", "./config/prompts/construction.yaml"))
        prompt = builder.build(
            detections_summary="스트레스 테스트 프레임",
            recent_events="이전 이벤트 없음",
        )
        analysis = provider.analyze(buf.tobytes(), prompt)
        return {
            "available": True,
            "description": analysis.description[:100],
            "risk_level": analysis.risk_level,
            "inference_time_ms": analysis.inference_time_ms,
        }
    except Exception as exc:
        return {"available": False, "error": str(exc)[:100]}


def main() -> int:
    parser = argparse.ArgumentParser(description="비디오 파일 기반 통합 테스트 (Phase 9 M3)")
    parser.add_argument("--video", default="./data/sample.mp4", help="테스트 비디오 파일 경로")
    parser.add_argument("--duration", type=int, default=30, help="테스트 지속 시간 (초)")
    parser.add_argument("--cameras", type=int, default=1, help="시뮬레이션 카메라 ID 수 (1~4, single-thread: camera_id만 분산)")
    parser.add_argument("--max-frames", type=int, default=0, help="최대 프레임 수 (0=무제한)")
    parser.add_argument("--policy-mode", action="store_true", help="MultiModelRegistry + EventPolicyEngine 모드")
    parser.add_argument("--with-vlm", action="store_true", help="VLM 호출 포함")
    parser.add_argument("--vlm-interval", type=int, default=10, help="VLM 호출 주기 (프레임 수)")
    parser.add_argument("--skip-frames", type=int, default=1, help="프레임 스킵 간격")
    parser.add_argument("--config", default="./config/default.yaml", help="App config 경로")
    parser.add_argument("--model-profiles", default="./config/model_profiles.yaml")
    parser.add_argument("--event-policies", default="./config/event_policies.yaml")
    parser.add_argument("--json-out", default=None, help="결과 JSON 저장 경로 (예: reports/stress_20260608.json)")
    parser.add_argument("--log-level", default="WARNING", help="로깅 레벨")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    # ONNX Runtime 경고 숨김
    logging.getLogger("onnxruntime").setLevel(logging.ERROR)

    if not Path(args.video).exists():
        print(f"ERROR: 비디오 파일 없음: {args.video}", file=sys.stderr)
        return 1

    # 비디오 로드
    cap = _load_video(args.video, loop=True)
    if cap is None:
        print(f"ERROR: 비디오 열기 실패: {args.video}", file=sys.stderr)
        return 1

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    print(f"[스트레스 테스트 시작]")
    print(f"  비디오: {args.video} ({width}x{height} @ {fps:.1f}fps, {total_frames}프레임)")
    print(f"  지속 시간: {args.duration}초")
    print(f"  카메라 수: {args.cameras}")
    print(f"  모드: {'policy' if args.policy_mode else 'legacy'}")
    print(f"  VLM: {'ON' if args.with_vlm else 'OFF'}")
    print(f"  프레임 스킵: {args.skip_frames}")

    # 프레임 미리 로드 (메모리에 모두 로드)
    print(f"\n프레임 로드 중...")
    frames = _read_frames(cap, max_frames=total_frames, loop=False)
    cap.release()
    if not frames:
        print("ERROR: 비디오에서 프레임을 읽을 수 없음", file=sys.stderr)
        return 1
    print(f"  {len(frames)}프레임 로드됨")

    # 파이프라인 컴포넌트 1회 생성
    print(f"\n파이프라인 컴포넌트 로드 중...")
    components = _create_pipeline(args)
    if args.policy_mode:
        print(f"  활성 모델: {components['registry'].active_model_names}")

    # 테스트 실행
    start = time.time()
    deadline = start + args.duration
    frame_idx = 0
    frame_count = 0
    vlm_count = 0
    inference_times: List[float] = []
    total_times: List[float] = []
    event_type_counter: Counter = Counter()
    alert_level_counter: Counter = Counter()
    failed_frames: List[int] = []
    vlm_results: List[Dict[str, Any]] = []

    print(f"\n[실행 중...]")
    while time.time() < deadline and (args.max_frames == 0 or frame_count < args.max_frames):
        frame = frames[frame_idx % len(frames)]
        frame_idx += 1

        if frame_idx % args.skip_frames != 0:
            continue

        # 카메라 시뮬레이션 (camera_id를 N개 중 하나로 분산)
        cam_id = f"stress_cam_{frame_count % args.cameras}"
        try:
            result = _run_single_frame_pipeline(frame, args, components, cam_id=cam_id)
            inference_times.append(result["inference_ms"])
            total_times.append(result["total_ms"])

            if "policy_decisions" in result:
                for dec in result["policy_decisions"]:
                    event_type_counter[dec["event_type"]] += 1
                    alert_level_counter[dec["alert_level"]] += 1
        except Exception as exc:
            failed_frames.append(frame_count)
            if len(failed_frames) <= 5:
                print(f"  프레임 {frame_count} 처리 실패: {exc}", file=sys.stderr)

        # VLM 호출 (간격마다)
        if args.with_vlm and frame_count > 0 and frame_count % args.vlm_interval == 0:
            vlm_result = _maybe_run_vlm(frame)
            if vlm_result:
                vlm_count += 1
                vlm_results.append(vlm_result)

        frame_count += 1

        if frame_count % 10 == 0:
            elapsed = time.time() - start
            fps_actual = frame_count / elapsed if elapsed > 0 else 0
            print(f"  ... {frame_count}프레임 처리 ({fps_actual:.1f}fps)")

    total_elapsed = time.time() - start

    # 결과 집계
    summary = {
        "ok": True,
        "video": args.video,
        "duration_sec": round(total_elapsed, 1),
        "mode": "policy" if args.policy_mode else "legacy",
        "simulated_cameras": args.cameras,
        "frames_processed": frame_count,
        "fps_actual": round(frame_count / total_elapsed, 2) if total_elapsed > 0 else 0,
        "failed_frames": len(failed_frames),
        "inference_ms": {
            "min": round(min(inference_times), 1) if inference_times else 0,
            "max": round(max(inference_times), 1) if inference_times else 0,
            "avg": round(sum(inference_times) / len(inference_times), 1) if inference_times else 0,
        },
        "total_ms": {
            "min": round(min(total_times), 1) if total_times else 0,
            "max": round(max(total_times), 1) if total_times else 0,
            "avg": round(sum(total_times) / len(total_times), 1) if total_times else 0,
        },
        "events_by_type": dict(event_type_counter),
        "events_by_alert_level": dict(alert_level_counter),
    }

    if args.with_vlm:
        vlm_latencies = [r.get("inference_time_ms", 0) for r in vlm_results if "inference_time_ms" in r]
        vlm_available = [r for r in vlm_results if r.get("available")]
        summary["vlm"] = {
            "enabled": True,
            "calls": vlm_count,
            "available": len(vlm_available),
            "unavailable": vlm_count - len(vlm_available),
            "latency_ms": {
                "min": round(min(vlm_latencies), 1) if vlm_latencies else 0,
                "max": round(max(vlm_latencies), 1) if vlm_latencies else 0,
                "avg": round(sum(vlm_latencies) / len(vlm_latencies), 1) if vlm_latencies else 0,
            } if vlm_latencies else None,
        }

    print(f"\n=== 스트레스 테스트 결과 ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[결과 저장] {out_path}")

    # 첫 5개 실패한 프레임 인덱스
    if failed_frames:
        print(f"\n실패한 프레임 (처음 5개): {failed_frames[:5]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
