#!/usr/bin/env python3
"""Run fail-closed CUDA inference qualification for Appliance ONNX models."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort


def _input_shape(input_meta: Any) -> tuple[int, ...]:
    defaults = (1, 3, 640, 640)
    return tuple(
        dimension if isinstance(dimension, int) and dimension > 0 else defaults[index] if index < 4 else 1
        for index, dimension in enumerate(input_meta.shape)
    )


def _input_dtype(type_name: str) -> np.dtype[Any]:
    if type_name == "tensor(float16)":
        return np.dtype(np.float16)
    if type_name == "tensor(float)":
        return np.dtype(np.float32)
    raise ValueError(f"unsupported model input type: {type_name}")


def _gpu_identity() -> str:
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode:
        raise RuntimeError("nvidia-smi did not identify an available GPU")
    return result.stdout.strip()


def qualify_model(path: Path) -> dict[str, Any]:
    options = ort.SessionOptions()
    # ORT's static CPU fallback prohibition rejects harmless graph bookkeeping before
    # inference. Profile the actual runs instead, then reject every non-CUDA node event.
    options.enable_profiling = True
    session = ort.InferenceSession(str(path), sess_options=options, providers=["CUDAExecutionProvider"])
    providers = session.get_providers()
    # ORT registers CPU as a control-flow fallback provider with CUDA sessions.
    # CUDA must be primary; the profile below proves every executed model node used it.
    if not providers or providers[0] != "CUDAExecutionProvider":
        raise RuntimeError(f"{path.name} did not prioritize CUDA: {providers}")

    inputs: dict[str, np.ndarray[Any, Any]] = {}
    input_summary: list[dict[str, Any]] = []
    for input_meta in session.get_inputs():
        shape = _input_shape(input_meta)
        dtype = _input_dtype(input_meta.type)
        inputs[input_meta.name] = np.zeros(shape, dtype=dtype)
        input_summary.append({"name": input_meta.name, "shape": list(shape), "type": input_meta.type})

    durations_ms: list[float] = []
    outputs: list[np.ndarray[Any, Any]] = []
    for _ in range(2):
        started = time.perf_counter()
        outputs = session.run(None, inputs)
        durations_ms.append(round((time.perf_counter() - started) * 1000, 3))
    profile_path = Path(session.end_profiling())
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    finally:
        profile_path.unlink(missing_ok=True)
    node_providers = {
        event["args"]["provider"]
        for event in profile
        if event.get("cat") == "Node" and "provider" in event.get("args", {})
    }
    if node_providers != {"CUDAExecutionProvider"}:
        raise RuntimeError(f"{path.name} executed non-CUDA nodes: {sorted(node_providers)}")
    output_summary = []
    for output_meta, output in zip(session.get_outputs(), outputs, strict=True):
        if not np.isfinite(output).all():
            raise RuntimeError(f"{path.name} produced non-finite values in {output_meta.name}")
        output_summary.append({"name": output_meta.name, "shape": list(output.shape), "type": str(output.dtype)})

    return {
        "path": path.name,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "providers": providers,
        "node_providers": sorted(node_providers),
        "inputs": input_summary,
        "outputs": output_summary,
        "run_latency_ms": durations_ms,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Require CUDA-only ONNX inference for all listed models")
    parser.add_argument("models", nargs="+", type=Path)
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        if "CUDAExecutionProvider" not in ort.get_available_providers():
            raise RuntimeError(f"CUDAExecutionProvider is unavailable: {ort.get_available_providers()}")
        missing = [str(path) for path in args.models if not path.is_file()]
        if missing:
            raise RuntimeError(f"required model files are missing: {', '.join(missing)}")
        report = {
            "onnxruntime_version": ort.__version__,
            "available_providers": ort.get_available_providers(),
            "gpu": _gpu_identity(),
            "models": [qualify_model(path) for path in args.models],
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
