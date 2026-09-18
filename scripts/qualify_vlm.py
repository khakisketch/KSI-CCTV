"""단일 프레임 VLM 자격 하네스 (백로그 B3).

타깃 어플라이언스에서 release-gate VLM 자격 증거로 사용한다. 단일 이미지를
`samples`회 순차 호출해 지연 분포·실패 모드를 측정하고, deadline 대비
pass/fail(`qualified`)을 JSON 리포트로 기록한다.

호출 경로는 프로덕션과 동일하다: `OllamaProvider.analyze` +
`ContextBuilder.build` (기존 사용례 `scripts/e2e_smoke_test.py::_maybe_run_vlm`,
프로덕션 형태 `src/pipeline/pipeline.py::_analyze_with_vlm` 참조).
"""
from __future__ import annotations

import argparse
import json
import math
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.vlm.context_builder import ContextBuilder  # noqa: E402
from src.vlm.ollama_provider import OllamaProvider  # noqa: E402

DEFAULT_PROMPT_TEMPLATE = "./config/prompts/construction.yaml"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="단일 프레임 VLM 자격 측정 하네스")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--model", required=True, help="자격 측정 대상 모델명(필수)")
    parser.add_argument("--image", required=True, help="단일 이미지 경로(필수)")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--timeout-sec", type=float, default=60.0)
    parser.add_argument("--deadline-sec", type=float, default=None)
    parser.add_argument("--json-out", default="reports/vlm-qualification.json")
    return parser.parse_args(argv)


def _collect_host() -> dict[str, Any]:
    """호스트 식별 정보. GPU는 nvidia-smi가 있을 때만 기록한다."""
    gpu: str | None = None
    try:
        proc = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            gpu = proc.stdout.strip()
    except Exception:
        gpu = None
    return {
        "machine": platform.node(),
        "platform": platform.platform(),
        "gpu": gpu,
    }


def _percentile(sorted_values: list[float], pct: float) -> float:
    """nearest-rank 백분위수 (0 < pct <= 100)."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _classify_exception(exc: BaseException) -> str:
    """호출 예외를 VLM failure_kind 계약으로 분류한다."""
    if isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
        return "timeout"
    return "provider_unavailable"


def _build_prompt() -> tuple[Any, str]:
    """프로덕션과 동일한 프롬프트를 구성한다 (빈 탐지 요약 + 최근 이벤트 없음)."""
    builder = ContextBuilder(DEFAULT_PROMPT_TEMPLATE)
    prompt = builder.build(
        detections_summary=ContextBuilder.format_detections([]),
        recent_events="최근 이벤트 없음",
    )
    return builder, prompt


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.samples < 1:
        print("오류: --samples는 1 이상이어야 합니다.", file=sys.stderr)
        return 2
    if args.timeout_sec <= 0:
        print("오류: --timeout-sec는 0보다 커야 합니다.", file=sys.stderr)
        return 2
    deadline_sec = args.deadline_sec if args.deadline_sec is not None else args.timeout_sec

    import cv2

    frame = cv2.imread(args.image)
    if frame is None:
        print(f"오류: 이미지를 불러올 수 없습니다: {args.image}", file=sys.stderr)
        return 2
    ok, buffer = cv2.imencode(".jpg", frame)
    if not ok:
        print(f"오류: 이미지 JPEG 인코딩에 실패했습니다: {args.image}", file=sys.stderr)
        return 2
    image_bytes = buffer.tobytes()

    try:
        _, prompt = _build_prompt()
    except Exception as exc:
        print(f"오류: 프롬프트 구성에 실패했습니다: {type(exc).__name__}", file=sys.stderr)
        return 2

    try:
        provider = OllamaProvider(
            base_url=args.base_url,
            model=args.model,
            timeout_sec=args.timeout_sec,
        )
        provider_error: BaseException | None = None
    except Exception as exc:
        provider = None
        provider_error = exc

    samples: list[dict[str, Any]] = []
    for index in range(args.samples):
        start = time.monotonic()
        if provider is None:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            samples.append(
                {
                    "index": index,
                    "latency_ms": latency_ms,
                    "success": False,
                    "failure_kind": _classify_exception(provider_error or RuntimeError("생성 실패")),
                    "parse_success": False,
                    "verdict": "error",
                }
            )
            continue
        try:
            result = provider.analyze(image_bytes, prompt)
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            success = bool(result.parse_success)
            samples.append(
                {
                    "index": index,
                    "latency_ms": latency_ms,
                    "success": success,
                    "failure_kind": str(getattr(result, "failure_kind", "") or ""),
                    "parse_success": bool(result.parse_success),
                    "verdict": str(getattr(result, "verdict", "error")),
                }
            )
        except Exception as exc:
            latency_ms = round((time.monotonic() - start) * 1000, 1)
            samples.append(
                {
                    "index": index,
                    "latency_ms": latency_ms,
                    "success": False,
                    "failure_kind": _classify_exception(exc),
                    "parse_success": False,
                    "verdict": "error",
                }
            )

    latencies = sorted(s["latency_ms"] for s in samples)
    success_count = sum(1 for s in samples if s["success"])
    failure_kinds: dict[str, int] = {}
    for s in samples:
        if not s["success"]:
            kind = s["failure_kind"] or "unknown"
            failure_kinds[kind] = failure_kinds.get(kind, 0) + 1
    stats = {
        "count": len(samples),
        "min_ms": latencies[0] if latencies else 0.0,
        "avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "p50_ms": _percentile(latencies, 50),
        "p95_ms": _percentile(latencies, 95),
        "max_ms": latencies[-1] if latencies else 0.0,
        "success_rate": success_count / len(samples) if samples else 0.0,
        "failure_kinds": failure_kinds,
    }
    qualified = bool(stats["success_rate"] == 1.0 and stats["p95_ms"] <= deadline_sec * 1000)

    report = {
        "model": args.model,
        "base_url": args.base_url,
        "host": _collect_host(),
        "samples": samples,
        "stats": stats,
        "deadline_sec": deadline_sec,
        "timeout_sec": args.timeout_sec,
        "qualified": qualified,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        out_path = Path(args.json_out)
        if out_path.parent != Path("."):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"오류: 리포트 기록에 실패했습니다: {exc}", file=sys.stderr)
        return 1
    return 0 if qualified else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"오류: 자격 측정 중 예상치 못한 실패: {type(exc).__name__}", file=sys.stderr)
        raise SystemExit(1) from exc
