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
# 추천 공개 모델(docs/model-sourcing.md). ⚠️ Ultralytics 가중치는 AGPL-3.0 — 상용 배포 시 검토.
# repo_id=Hugging Face 다운로드, url=직접 .pt 다운로드(GitHub raw 등).
SAFETY_MODELS: dict[str, dict[str, any]] = {
    "ppe_yolo": {
        # Hansung-Cho/yolov8-ppe-detection (10-class, helmet+vest 지원). ungated.
        "repo_id": "Hansung-Cho/yolov8-ppe-detection",
        "output_name": "yolo26_ppe.onnx",
        "imgsz": 640,
        "half": True,
        "class_map": {
            "Hardhat": "helmet",
            "NO-Hardhat": "no_helmet",
            "Safety Vest": "vest",
            "NO-Safety Vest": "no_vest",
            "Person": "person",
            # Mask/NO-Mask/Safety Cone/machinery/vehicle는 KSI 표준 외 → 미매핑
        },
    },
    "fire_smoke_yolo": {
        # Flare Guard YOLO11n (Fire, Smoke, 일반 장면). GitHub raw 직접 다운로드.
        "url": "https://raw.githubusercontent.com/sayedgamal99/Real-Time-Smoke-Fire-Detection-YOLO11/main/models/best_nano_111.pt",
        # 다운로드 무결성 검증용 SHA-256(2026-06-15 확인본). 업스트림이 변경되면 불일치로 거부되며,
        # 검토 후 새 다이제스트로 갱신한다(MITM·임의 변경 방지).
        "expected_sha256": "3cbeb9569d8c4057734a81437b90c3e95e55ebdd40a5c5ea7dd7873a247cef13",
        "output_name": "yolo26_fire_smoke.onnx",
        "imgsz": 640,
        "half": True,
        "class_map": {
            "Fire": "fire",
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


def _download_url(url: str, dest_dir: Path) -> Path | None:
    """직접 URL(.pt)을 다운로드해 저장 경로를 반환(GitHub raw 등 HF 외 출처)."""
    try:
        import urllib.request
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / Path(url).name
        logger.info("Downloading %s ...", url)
        urllib.request.urlretrieve(url, dest)  # noqa: S310 (신뢰된 모델 URL)
        logger.info("Downloaded to %s", dest)
        return dest
    except Exception as exc:
        logger.error("URL download failed: %s", exc)
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
        # Appliance inference is fixed at 640x640. Dynamic shape graphs leave Range/Shape
        # nodes on the CPU EP, so make the deployment contract explicit.
        model.export(format="onnx", imgsz=imgsz, half=half, dynamic=False, simplify=True, opset=18)
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

    repo_id = config.get("repo_id")
    url = config.get("url")
    source = repo_id or url
    logger.info("[%s] Fetching %s ...", name, source)

    if not _has_ultralytics():
        logger.warning("ultralytics not installed. Cannot export to ONNX.")
        _print_manual_instructions(name, source, config["output_name"])
        return False

    download_dir = MODELS_DIR / f"_download_{name}"
    download_dir.mkdir(parents=True, exist_ok=True)

    if url:
        # 직접 URL(.pt) 다운로드 (GitHub raw 등)
        pt_path = _download_url(url, download_dir)
    else:
        if not _has_hf_hub():
            logger.warning("huggingface_hub not installed. Cannot auto-download.")
            _print_manual_instructions(name, source, config["output_name"])
            return False
        model_dir = _download_hf(repo_id, download_dir)
        pt_path = _find_best_ckpt(model_dir) if model_dir is not None else None

    if pt_path is None:
        logger.error("[%s] 체크포인트 다운로드 실패", name)
        _print_manual_instructions(name, source, config["output_name"])
        return False

    # 다운로드 무결성: expected_sha256이 지정된 모델은 다이제스트가 일치해야 한다(MITM/임의변경 거부).
    expected = config.get("expected_sha256")
    if expected:
        import hashlib
        actual = hashlib.sha256(Path(pt_path).read_bytes()).hexdigest()
        if actual != expected:
            logger.error(
                "[%s] SHA-256 불일치 — 다운로드 거부. expected=%s actual=%s. "
                "업스트림이 갱신됐다면 docs/model-sourcing.md 절차대로 검토 후 SAFETY_MODELS의 "
                "expected_sha256을 새 값으로 갱신하세요(MITM이 아닌 정상 변경 확인 필수).",
                name, expected, actual,
            )
            return False
        logger.info("[%s] SHA-256 검증 통과", name)
    else:
        # 핀 누락을 가시화 — 무결성 검증 없이 진행됨(HF etag/revision에만 의존). #6
        import hashlib
        actual = hashlib.sha256(Path(pt_path).read_bytes()).hexdigest()
        logger.warning(
            "[%s] expected_sha256 미지정 — 무결성 검증 생략. 신뢰되면 SAFETY_MODELS에 핀 추가 권장: %s",
            name, actual,
        )

    logger.info("[%s] Found checkpoint: %s", name, pt_path)

    if not _export_to_onnx(pt_path, output_path, imgsz=config.get("imgsz", 640), half=config.get("half", True)):
        _print_manual_instructions(name, source, config["output_name"])
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
            # fire_smoke_yolo는 repo_id 없이 url만 가질 수 있어 안전 접근(KeyError 방지)
            "source": config.get("repo_id") or config.get("url"),
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
