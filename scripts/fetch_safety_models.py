#!/usr/bin/env python3
"""Fetch pre-trained safety detection models and export to ONNX.

Phase 8 Track A: 사전학습된 공개 모델을 다운로드하고 ONNX로 변환하여
KSI-CCTV 파이프라인에 통합한다.

Models:
- PPE (hard-hat): keremberke/yolov8n-hard-hat-detection
- Fire: touati-kamel/yolov8s-forest-fire-detection
  - 원래 keremberke/yolov8n-fire-detection 시도 → 401 Unauthorized
  - 대체 repo로 변경 (Phase 9 M4)
  - 9-class forest fire detection 모델 (fire 중심으로 smoke는 VLM fallback)

사용 예:
    python scripts/fetch_safety_models.py            # 모든 모델
    python scripts/fetch_safety_models.py --all      # 명시적 전체
    python scripts/fetch_safety_models.py --model ppe_yolo
    python scripts/fetch_safety_models.py --force    # 기존 ONNX 덮어쓰기

This script requires internet access and the ultralytics package.
If automatic download fails, manual download instructions are printed.
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
MODELS_DIR = ROOT / "models"

# Model definitions: model_id → config
SAFETY_MODELS: dict[str, dict[str, any]] = {
    "ppe_yolo": {
        "repo_id": "keremberke/yolov8n-hard-hat-detection",
        "output_name": "yolo26_ppe.onnx",
        "imgsz": 640,
        "half": True,
        "class_map": {
            "Hardhat": "helmet",
            "NO-Hardhat": "no_helmet",
            "Helmet": "helmet",
            "NO-Helmet": "no_helmet",
        },
    },
    "fire_smoke_yolo": {
        "repo_id": "touati-kamel/yolov8s-forest-fire-detection",
        "output_name": "yolo26_fire_smoke.onnx",
        "imgsz": 640,
        "half": True,
        "class_map": {
            "fire": "fire",
            "Fire": "fire",
            "smoke": "smoke",
            "Smoke": "smoke",
        },
    },
}


def _has_hf_hub() -> bool:
    try:
        import huggingface_hub  # noqa: F401
        return True
    except Exception:
        return False


def _has_ultralytics() -> bool:
    try:
        import ultralytics  # noqa: F401
        return True
    except Exception:
        return False


def _download_hf(repo_id: str, local_dir: Path) -> Path | None:
    """Download model from Hugging Face Hub."""
    try:
        from huggingface_hub import snapshot_download
        logger.info("Downloading %s ...", repo_id)
        path = snapshot_download(repo_id=repo_id, local_dir=str(local_dir), local_dir_use_symlinks=False)
        logger.info("Downloaded to %s", path)
        return Path(path)
    except Exception as exc:
        logger.error("Failed to download %s: %s", repo_id, exc)
        return None


def _find_best_ckpt(model_dir: Path) -> Path | None:
    """Find the best checkpoint (.pt) in the downloaded directory."""
    candidates = list(model_dir.rglob("*.pt"))
    if not candidates:
        return None
    # Prefer files with "best" in name, then largest file
    best_candidates = [p for p in candidates if "best" in p.name.lower()]
    if best_candidates:
        return max(best_candidates, key=lambda p: p.stat().st_size)
    return max(candidates, key=lambda p: p.stat().st_size)


def _export_to_onnx(pt_path: Path, output_path: Path, imgsz: int = 640, half: bool = True) -> bool:
    """Export PyTorch model to ONNX using ultralytics."""
    try:
        from ultralytics import YOLO
        logger.info("Loading %s ...", pt_path)
        model = YOLO(str(pt_path))
        logger.info("Exporting to ONNX (imgsz=%s, half=%s) ...", imgsz, half)
        model.export(format="onnx", imgsz=imgsz, half=half, simplify=True)
        # Export creates a file next to the .pt with .onnx extension
        expected = pt_path.with_suffix(".onnx")
        if expected.exists():
            expected.rename(output_path)
            logger.info("Exported ONNX: %s", output_path)
            return True
        logger.error("ONNX export did not create expected file: %s", expected)
        return False
    except Exception as exc:
        logger.error("ONNX export failed: %s", exc)
        return False


def _validate_onnx(onnx_path: Path) -> bool:
    """Quick ONNX validation: load session and check inputs/outputs."""
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(onnx_path))
        inputs = sess.get_inputs()
        outputs = sess.get_outputs()
        logger.info(
            "ONNX validation OK: inputs=%s, outputs=%s",
            [(i.name, i.type, i.shape) for i in inputs],
            [(o.name, o.type, o.shape) for o in outputs],
        )
        return True
    except Exception as exc:
        logger.error("ONNX validation failed: %s", exc)
        return False


def _print_manual_instructions(name: str, repo_id: str, output_name: str) -> None:
    """Print manual download and export instructions."""
    print(f"""
=== Manual setup for {name} ===
1. Visit: https://huggingface.co/{repo_id}
2. Download the latest .pt checkpoint
3. Place it in: models/
4. Export to ONNX:
   python -c "from ultralytics import YOLO; YOLO('./models/XXXX.pt').export(format='onnx', imgsz=640, half=True)"
5. Rename the output to: models/{output_name}
""")


def fetch_model(name: str, config: dict[str, any], skip_existing: bool = True) -> bool:
    """Fetch a single model: download, export, validate."""
    output_path = MODELS_DIR / config["output_name"]
    if skip_existing and output_path.exists():
        logger.info("[%s] ONNX model already exists: %s", name, output_path)
        return _validate_onnx(output_path)

    repo_id = config["repo_id"]
    logger.info("[%s] Fetching %s ...", name, repo_id)

    if not _has_hf_hub():
        logger.warning("huggingface_hub not installed. Cannot auto-download.")
        _print_manual_instructions(name, repo_id, config["output_name"])
        return False

    if not _has_ultralytics():
        logger.warning("ultralytics not installed. Cannot export to ONNX.")
        _print_manual_instructions(name, repo_id, config["output_name"])
        return False

    download_dir = MODELS_DIR / f"_download_{name}"
    download_dir.mkdir(parents=True, exist_ok=True)

    model_dir = _download_hf(repo_id, download_dir)
    if model_dir is None:
        _print_manual_instructions(name, repo_id, config["output_name"])
        return False

    pt_path = _find_best_ckpt(model_dir)
    if pt_path is None:
        logger.error("No .pt checkpoint found in %s", model_dir)
        _print_manual_instructions(name, repo_id, config["output_name"])
        return False

    logger.info("[%s] Found checkpoint: %s", name, pt_path)

    if not _export_to_onnx(pt_path, output_path, imgsz=config.get("imgsz", 640), half=config.get("half", True)):
        _print_manual_instructions(name, repo_id, config["output_name"])
        return False

    if not _validate_onnx(output_path):
        logger.error("[%s] Validation failed. Removing %s", name, output_path)
        output_path.unlink(missing_ok=True)
        return False

    logger.info("[%s] Successfully fetched and validated: %s", name, output_path)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch pre-trained safety models and export to ONNX")
    parser.add_argument("--all", action="store_true", help="Fetch all models (default behavior)")
    parser.add_argument("--model", choices=list(SAFETY_MODELS.keys()), help="Fetch a specific model only")
    parser.add_argument("--skip-existing", action="store_true", default=True, help="Skip if ONNX already exists")
    parser.add_argument("--force", action="store_true", help="Re-fetch even if ONNX exists")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    skip_existing = args.skip_existing and not args.force

    results: dict[str, dict[str, any]] = {}
    # --model 지정 시 해당 모델만, 아니면(--all/기본) 전체
    models_to_fetch = {args.model: SAFETY_MODELS[args.model]} if args.model else SAFETY_MODELS

    for name, config in models_to_fetch.items():
        ok = fetch_model(name, config, skip_existing=skip_existing)
        results[name] = {
            "ok": ok,
            "output": str(MODELS_DIR / config["output_name"]),
            "repo_id": config["repo_id"],
        }

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("\n=== Summary ===")
        for name, result in results.items():
            status = "OK" if result["ok"] else "FAILED"
            print(f"  [{status}] {name}: {result['output']}")

    return 0 if all(r["ok"] for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
