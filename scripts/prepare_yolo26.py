"""YOLO26 ONNX 모델 준비 스크립트.

사용 예:
    python3 scripts/prepare_yolo26.py --model yolo26n.pt
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path


def find_exported_onnx(model: str, output: Path, before_files: set[Path]) -> Path | None:
    """Ultralytics export 결과 ONNX 파일 탐색"""
    candidates = []

    model_path = Path(model)
    candidates.append(model_path.with_suffix(".onnx"))
    candidates.append(Path(str(model).replace(".pt", ".onnx")))
    candidates.append(Path.cwd() / model_path.with_suffix(".onnx").name)

    for candidate in candidates:
        if candidate.exists() and candidate.resolve() != output.resolve():
            return candidate

    after_files = {path for path in Path.cwd().rglob("*.onnx") if path.is_file()}
    new_files = [path for path in after_files - before_files if path.resolve() != output.resolve()]
    if new_files:
        return max(new_files, key=lambda p: p.stat().st_mtime)

    existing = [path for path in after_files if path.resolve() != output.resolve()]
    if existing:
        return max(existing, key=lambda p: p.stat().st_mtime)

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Export YOLO26 model to ONNX")
    parser.add_argument("--model", default="yolo26n.pt", help="Ultralytics model name or local .pt path")
    parser.add_argument("--imgsz", default="640", help="Image size for export")
    parser.add_argument("--device", default="0", help="CUDA device id or cpu")
    parser.add_argument("--output", default="models/yolo26.onnx", help="Output ONNX path")
    parser.add_argument("--no-half", action="store_true", help="Disable FP16 export")
    args = parser.parse_args()

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    command = [
        "yolo",
        "export",
        f"model={args.model}",
        "format=onnx",
        f"imgsz={args.imgsz}",
        f"device={args.device}",
    ]
    if not args.no_half:
        command.append("half=True")

    before_files = {path for path in Path.cwd().rglob("*.onnx") if path.is_file()}
    print("Running:", " ".join(command))
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        return result.returncode

    exported = find_exported_onnx(args.model, output, before_files)
    if exported and exported.exists():
        if exported.resolve() != output.resolve():
            shutil.move(str(exported), str(output))
        print(f"YOLO26 ONNX saved to {output}")
        return 0

    print("Export succeeded but ONNX file was not found. Check Ultralytics output directory.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
