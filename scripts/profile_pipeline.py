#!/usr/bin/env python3
"""
KSI-CCTV 파이프라인 성능 프로파일링 도구

cProfile + tracemalloc을 사용하여 모델별 추론 시간, 메모리 사용량을 측정합니다.
"""

import argparse
import cProfile
import io
import json
import os
import pstats
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any

import cv2
import numpy as np

# 프로젝트 루트를 sys.path에 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.detection.model_registry import MultiModelRegistry
from src.pipeline.pipeline import SafetyPipeline
from src.policy.event_policy_engine import EventPolicyEngine


def load_test_frames(video_path: str, max_frames: int = 100) -> list[np.ndarray]:
    """비디오 파일에서 테스트용 프레임 로드"""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"비디오 파일을 열 수 없습니다: {video_path}")

    frames = []
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, total_frames // max_frames)

    for i in range(0, total_frames, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ret, frame = cap.read()
        if not ret:
            break
        frames.append(frame)
        if len(frames) >= max_frames:
            break

    cap.release()
    if not frames:
        raise RuntimeError("프레임을 읽을 수 없습니다")
    return frames


def create_synthetic_frames(count: int = 50, width: int = 640, height: int = 480) -> list[np.ndarray]:
    """합성 테스트 프레임 생성 (비디오 파일 없을 때)"""
    frames = []
    for i in range(count):
        # 노이즈 + 움직이는 사각형 시뮬레이션
        frame = np.random.randint(0, 50, (height, width, 3), dtype=np.uint8)
        x = (i * 10) % (width - 50)
        y = (i * 5) % (height - 50)
        cv2.rectangle(frame, (x, y), (x + 50, y + 50), (0, 255, 0), -1)
        cv2.putText(frame, f"Frame {i}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        frames.append(frame)
    return frames


def profile_model_registry(
    registry: MultiModelRegistry,
    frames: list[np.ndarray],
    iterations: int = 10
) -> dict[str, Any]:
    """MultiModelRegistry 개별 모델 프로파일링"""
    results = {}

    for model_name in registry.active_model_names:
        model = registry.models[model_name]
        latencies = []
        peak_memories = []

        for _ in range(iterations):
            for frame in frames:
                tracemalloc.start()
                t0 = time.perf_counter()
                model.detect(frame)  # 레이턴시 측정용 — 결과는 사용하지 않음
                latency_ms = (time.perf_counter() - t0) * 1000
                current, peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()

                latencies.append(latency_ms)
                peak_memories.append(peak / (1024 * 1024))  # MB

        results[model_name] = {
            "iterations": len(latencies),
            "avg_latency_ms": round(sum(latencies) / len(latencies), 2),
            "min_latency_ms": round(min(latencies), 2),
            "max_latency_ms": round(max(latencies), 2),
            "p50_latency_ms": round(sorted(latencies)[len(latencies) // 2], 2),
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
            "avg_peak_memory_mb": round(sum(peak_memories) / len(peak_memories), 2),
            "max_peak_memory_mb": round(max(peak_memories), 2),
        }

    return results


def profile_full_pipeline(
    pipeline: SafetyPipeline,
    frames: list[np.ndarray],
    camera_id: str = "profile_cam",
    iterations: int = 5
) -> dict[str, Any]:
    """전체 파이프라인 (detect + policy + VLM) 프로파일링"""
    latencies = []
    peak_memories = []
    detection_counts = []
    policy_decision_counts = []

    for _ in range(iterations):
        for frame in frames:
            tracemalloc.start()
            t0 = time.perf_counter()

            # detect_all
            all_detections = pipeline.model_registry.detect_all(frame)
            total_dets = sum(len(d) for d in all_detections.values())

            # policy evaluate
            decisions = pipeline.policy_engine.evaluate(
                profile_name="construction",
                model_detections=all_detections,
                frame=frame,
                camera_id=camera_id,
            )

            latency_ms = (time.perf_counter() - t0) * 1000
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            latencies.append(latency_ms)
            peak_memories.append(peak / (1024 * 1024))
            detection_counts.append(total_dets)
            policy_decision_counts.append(len(decisions))

    return {
        "iterations": len(latencies),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2),
        "min_latency_ms": round(min(latencies), 2),
        "max_latency_ms": round(max(latencies), 2),
        "p50_latency_ms": round(sorted(latencies)[len(latencies) // 2], 2),
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
        "avg_peak_memory_mb": round(sum(peak_memories) / len(peak_memories), 2),
        "max_peak_memory_mb": round(max(peak_memories), 2),
        "avg_detections_per_frame": round(sum(detection_counts) / len(detection_counts), 2),
        "avg_policy_decisions_per_frame": round(sum(policy_decision_counts) / len(policy_decision_counts), 2),
    }


def profile_with_cprofile(
    func,
    *args,
    **kwargs
) -> tuple:
    """cProfile로 함수 프로파일링"""
    profiler = cProfile.Profile()
    profiler.enable()
    result = func(*args, **kwargs)
    profiler.disable()

    # 통계 추출
    s = io.StringIO()
    ps = pstats.Stats(profiler, stream=s).sort_stats('cumulative')
    ps.print_stats(30)
    profile_text = s.getvalue()

    return result, profile_text


def main():
    parser = argparse.ArgumentParser(description="KSI-CCTV 파이프라인 성능 프로파일링")
    parser.add_argument("--video", type=str, help="테스트용 비디오 파일 경로")
    parser.add_argument("--frames", type=int, default=50, help="테스트 프레임 수 (합성 생성 시)")
    parser.add_argument("--iterations", type=int, default=10, help="모델별 반복 횟수")
    parser.add_argument("--pipeline-iterations", type=int, default=5, help="전체 파이프라인 반복 횟수")
    parser.add_argument("--cprofile", action="store_true", help="cProfile 상세 출력 활성화")
    parser.add_argument("--json-out", type=str, help="결과 JSON 저장 경로")
    parser.add_argument("--width", type=int, default=640, help="합성 프레임 너비")
    parser.add_argument("--height", type=int, default=480, help="합성 프레임 높이")
    args = parser.parse_args()

    print("=" * 60)
    print("KSI-CCTV 파이프라인 성능 프로파일링")
    print("=" * 60)

    # 테스트 프레임 준비
    if args.video and os.path.exists(args.video):
        print(f"[INFO] 비디오에서 프레임 로드: {args.video}")
        frames = load_test_frames(args.video, max_frames=args.frames)
    else:
        print(f"[INFO] 합성 프레임 생성: {args.frames}개 ({args.width}x{args.height})")
        frames = create_synthetic_frames(args.frames, args.width, args.height)

    print(f"[INFO] 테스트 프레임: {len(frames)}개")

    # 설정 및 컴포넌트 초기화 (YAML에서 로드)
    registry = MultiModelRegistry.from_config("./config/model_profiles.yaml")
    policy_engine = EventPolicyEngine.from_config("./config/event_policies.yaml")
    pipeline = SafetyPipeline(
        model_registry=registry,
        policy_engine=policy_engine,
    )

    print(f"[INFO] 활성 모델: {registry.active_model_names}")
    print(f"[INFO] 실패한 모델: {list(registry.failed_models.keys())}")

    # 1. 개별 모델 프로파일링
    print("\n" + "=" * 60)
    print("1. 개별 모델 프로파일링 (detect만)")
    print("=" * 60)

    if args.cprofile:
        model_results, profile_text = profile_with_cprofile(
            profile_model_registry, registry, frames, args.iterations
        )
        print("\n[cProfile 상위 30 함수]")
        print(profile_text)
    else:
        model_results = profile_model_registry(registry, frames, args.iterations)

    for model_name, stats in model_results.items():
        print(f"\n  [{model_name}]")
        print(f"    평균 레이턴시: {stats['avg_latency_ms']:.2f}ms (p50: {stats['p50_latency_ms']:.2f}, p95: {stats['p95_latency_ms']:.2f})")
        print(f"    최소/최대: {stats['min_latency_ms']:.2f} / {stats['max_latency_ms']:.2f}ms")
        print(f"    평균 피크 메모리: {stats['avg_peak_memory_mb']:.2f}MB (최대: {stats['max_peak_memory_mb']:.2f}MB)")

    # 2. 전체 파이프라인 프로파일링
    print("\n" + "=" * 60)
    print("2. 전체 파이프라인 프로파일링 (detect + policy)")
    print("=" * 60)

    if args.cprofile:
        pipeline_results, profile_text = profile_with_cprofile(
            profile_full_pipeline, pipeline, frames, "profile_cam", args.pipeline_iterations
        )
        print("\n[cProfile 상위 30 함수]")
        print(profile_text)
    else:
        pipeline_results = profile_full_pipeline(pipeline, frames, "profile_cam", args.pipeline_iterations)

    print(f"\n  평균 레이턴시: {pipeline_results['avg_latency_ms']:.2f}ms (p50: {pipeline_results['p50_latency_ms']:.2f}, p95: {pipeline_results['p95_latency_ms']:.2f})")
    print(f"  최소/최대: {pipeline_results['min_latency_ms']:.2f} / {pipeline_results['max_latency_ms']:.2f}ms")
    print(f"  평균 피크 메모리: {pipeline_results['avg_peak_memory_mb']:.2f}MB (최대: {pipeline_results['max_peak_memory_mb']:.2f}MB)")
    print(f"  프레임당 평균 탐지: {pipeline_results['avg_detections_per_frame']:.2f}개")
    print(f"  프레임당 평균 정책 결정: {pipeline_results['avg_policy_decisions_per_frame']:.2f}개")

    # 3. VLM 캐시 통계 (파이프라인 실행 후)
    if hasattr(pipeline, "get_vlm_cache_stats"):
        vlm_stats = pipeline.get_vlm_cache_stats()
        print("\n" + "=" * 60)
        print("3. VLM 캐시 통계")
        print("=" * 60)
        print(f"  Hits: {vlm_stats['hits']}, Misses: {vlm_stats['misses']}, Bypasses: {vlm_stats['bypasses']}")
        total = vlm_stats['hits'] + vlm_stats['misses']
        if total > 0:
            print(f"  Hit rate: {vlm_stats['hits'] / total * 100:.1f}%")

    # 결과 종합
    summary = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "test_frames": len(frames),
        "model_iterations": args.iterations,
        "pipeline_iterations": args.pipeline_iterations,
        "active_models": list(registry.active_model_names),
        "failed_models": list(registry.failed_models.keys()),
        "model_profiles": model_results,
        "pipeline_profile": pipeline_results,
    }

    if hasattr(pipeline, "get_vlm_cache_stats"):
        summary["vlm_cache_stats"] = pipeline.get_vlm_cache_stats()

    # JSON 저장
    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"\n[INFO] 결과 저장: {out_path}")

    print("\n" + "=" * 60)
    print("프로파일링 완료")
    print("=" * 60)


if __name__ == "__main__":
    main()
