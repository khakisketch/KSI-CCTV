#!/usr/bin/env python3
"""Dataset download script for KSI-CCTV custom model training.

Phase 8 Track B: 다양한 출처(Kaggle, Roboflow, URL)에서 데이터셋을 다운로드하고
YOLO 학습을 위한 표준 디렉토리 구조로 준비합니다.

Usage:
    python scripts/download_datasets.py --dataset ppe
    python scripts/download_datasets.py --dataset fire_smoke
    python scripts/download_datasets.py --all
"""
from __future__ import annotations

import argparse
import json
import logging
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


@dataclass
class DatasetSource:
    """Dataset source configuration."""
    name: str
    source_type: str  # kaggle, roboflow, url
    identifier: str  # kaggle dataset name, roboflow project, or URL
    target_dir: Path
    classes: List[str]
    description: str = ""
    api_key_env: Optional[str] = None  # Environment variable for API key


# Pre-configured dataset sources
DATASET_SOURCES: Dict[str, List[DatasetSource]] = {
    "ppe": [
        DatasetSource(
            name="hard_hat_workers",
            source_type="kaggle",
            identifier="andrewmvd/hard-hat-detection",
            target_dir=DATA_DIR / "ppe" / "hard_hat_workers",
            classes=["person", "helmet", "head"],
            description="Hard hat detection dataset from Kaggle",
        ),
        DatasetSource(
            name="construction_safety",
            source_type="kaggle",
            identifier="syedali110/construction-site-safety-image-dataset",
            target_dir=DATA_DIR / "ppe" / "construction_safety",
            classes=["person", "helmet", "vest", "no_helmet", "no_vest"],
            description="Construction site safety images with PPE labels",
        ),
    ],
    "fire_smoke": [
        DatasetSource(
            name="fire_detection",
            source_type="kaggle",
            identifier="rituparnabom/fire-detection-from-images",
            target_dir=DATA_DIR / "fire_smoke" / "fire_detection",
            classes=["fire", "no_fire"],
            description="Fire detection from images",
        ),
        DatasetSource(
            name="d_fire",
            source_type="url",
            identifier="https://github.com/gaiasd/DFire/archive/refs/heads/main.zip",
            target_dir=DATA_DIR / "fire_smoke" / "d_fire",
            classes=["fire", "smoke"],
            description="D-Fire dataset: fire and smoke detection",
        ),
    ],
}


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _download_kaggle(dataset_name: str, target_dir: Path) -> bool:
    """Download dataset from Kaggle using kagglehub."""
    try:
        import kagglehub
        logger.info("Downloading Kaggle dataset: %s", dataset_name)
        path = kagglehub.dataset_download(dataset_name)
        logger.info("Downloaded to: %s", path)
        
        # Move to target directory
        _ensure_dir(target_dir)
        downloaded_path = Path(path)
        if downloaded_path.is_dir():
            for item in downloaded_path.iterdir():
                dest = target_dir / item.name
                if dest.exists():
                    if dest.is_dir():
                        shutil.rmtree(dest)
                    else:
                        dest.unlink()
                shutil.move(str(item), str(dest))
        return True
    except ImportError:
        logger.error("kagglehub not installed. Install with: pip install kagglehub")
        logger.info("Alternative: Download manually from https://www.kaggle.com/datasets/%s", dataset_name)
        return False
    except Exception as exc:
        logger.error("Failed to download Kaggle dataset %s: %s", dataset_name, exc)
        return False


def _download_url(url: str, target_dir: Path) -> bool:
    """Download dataset from direct URL."""
    try:
        logger.info("Downloading from URL: %s", url)
        response = requests.get(url, stream=True, timeout=300)
        response.raise_for_status()
        
        # Determine file extension
        parsed = urlparse(url)
        filename = Path(parsed.path).name or "download.zip"
        temp_path = target_dir / f"_temp_{filename}"
        
        _ensure_dir(target_dir)
        
        # Download with progress
        total_size = int(response.headers.get("content-length", 0))
        downloaded = 0
        chunk_size = 8192
        
        with open(temp_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print(f"\rDownloading: {percent:.1f}%", end="", flush=True)
        print()  # New line after progress
        
        # Extract if zip
        if filename.endswith(".zip") or zipfile.is_zipfile(temp_path):
            logger.info("Extracting zip file...")
            with zipfile.ZipFile(temp_path, "r") as zf:
                zf.extractall(target_dir)
        else:
            # Move single file
            dest = target_dir / filename
            shutil.move(str(temp_path), str(dest))
        
        # Clean up temp file
        if temp_path.exists():
            temp_path.unlink()
        
        logger.info("Downloaded and extracted to: %s", target_dir)
        return True
        
    except Exception as exc:
        logger.error("Failed to download from URL %s: %s", url, exc)
        return False


def _download_roboflow(project: str, target_dir: Path, api_key: Optional[str] = None) -> bool:
    """Download dataset from Roboflow."""
    try:
        import roboflow
        logger.info("Downloading Roboflow project: %s", project)
        
        if api_key:
            rf = roboflow.Roboflow(api_key=api_key)
        else:
            rf = roboflow.Roboflow()
        
        # Format: workspace/project-version
        parts = project.split("/")
        if len(parts) != 2:
            logger.error("Invalid Roboflow project format. Expected: workspace/project")
            return False
        
        workspace, project_name = parts
        project = rf.workspace(workspace).project(project_name)
        dataset = project.version("latest").download("yolov8")
        
        # Move to target directory
        _ensure_dir(target_dir)
        downloaded_path = Path(dataset.location)
        for item in downloaded_path.iterdir():
            dest = target_dir / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))
        
        logger.info("Downloaded Roboflow dataset to: %s", target_dir)
        return True
        
    except ImportError:
        logger.error("roboflow not installed. Install with: pip install roboflow")
        return False
    except Exception as exc:
        logger.error("Failed to download Roboflow project %s: %s", project, exc)
        return False


def download_dataset(source: DatasetSource, api_key: Optional[str] = None) -> bool:
    """Download a single dataset source."""
    logger.info("=" * 60)
    logger.info("Dataset: %s (%s)", source.name, source.description)
    logger.info("Source: %s", source.source_type)
    logger.info("Target: %s", source.target_dir)
    
    if source.source_type == "kaggle":
        return _download_kaggle(source.identifier, source.target_dir)
    elif source.source_type == "roboflow":
        return _download_roboflow(source.identifier, source.target_dir, api_key)
    elif source.source_type == "url":
        return _download_url(source.identifier, source.target_dir)
    else:
        logger.error("Unknown source type: %s", source.source_type)
        return False


def generate_dataset_yaml(dataset_name: str, target_dir: Path, classes: List[str]) -> Path:
    """Generate Ultralytics YOLO dataset.yaml file."""
    yaml_path = target_dir / "dataset.yaml"
    
    yaml_content = f"""# KSI-CCTV Dataset Configuration
# Auto-generated by scripts/download_datasets.py
# 
# Classes: {', '.join(classes)}

path: {target_dir.absolute()}  # dataset root dir
train: train/images  # train images (relative to 'path')
val: val/images  # val images (relative to 'path')
test: test/images  # test images (optional)

# Classes
names:
{chr(10).join(f'  {i}: {cls}' for i, cls in enumerate(classes))}

# Download sources (for reference)
# This dataset was downloaded for KSI-CCTV Phase 8 custom model training
# See scripts/download_datasets.py for source configuration
"""
    
    yaml_path.write_text(yaml_content, encoding="utf-8")
    logger.info("Generated dataset.yaml: %s", yaml_path)
    return yaml_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Download datasets for KSI-CCTV custom model training")
    parser.add_argument("--dataset", choices=list(DATASET_SOURCES.keys()), help="Dataset category to download")
    parser.add_argument("--all", action="store_true", help="Download all datasets")
    parser.add_argument("--api-key", help="API key for Roboflow or other services")
    parser.add_argument("--list", action="store_true", help="List available datasets without downloading")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()
    
    if args.list:
        print("\nAvailable datasets:\n")
        for category, sources in DATASET_SOURCES.items():
            print(f"  {category}:")
            for source in sources:
                print(f"    - {source.name}: {source.description}")
                print(f"      Source: {source.source_type} ({source.identifier})")
                print(f"      Classes: {', '.join(source.classes)}")
                print()
        return 0
    
    if not args.dataset and not args.all:
        parser.print_help()
        return 1
    
    datasets_to_download = list(DATASET_SOURCES.keys()) if args.all else [args.dataset]
    
    results: Dict[str, List[Dict[str, any]]] = {}
    
    for category in datasets_to_download:
        sources = DATASET_SOURCES.get(category, [])
        category_results = []
        
        for source in sources:
            ok = download_dataset(source, api_key=args.api_key)
            
            # Generate dataset.yaml if download succeeded
            if ok:
                yaml_path = generate_dataset_yaml(source.name, source.target_dir, source.classes)
                category_results.append({
                    "name": source.name,
                    "ok": True,
                    "path": str(source.target_dir),
                    "yaml": str(yaml_path),
                    "classes": source.classes,
                })
            else:
                category_results.append({
                    "name": source.name,
                    "ok": False,
                    "path": str(source.target_dir),
                    "error": "Download failed",
                })
        
        results[category] = category_results
    
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
    else:
        print("\n" + "=" * 60)
        print("Download Summary")
        print("=" * 60)
        for category, category_results in results.items():
            print(f"\n{category}:")
            for result in category_results:
                status = "OK" if result["ok"] else "FAILED"
                print(f"  [{status}] {result['name']}: {result['path']}")
                if result["ok"]:
                    print(f"      Classes: {', '.join(result['classes'])}")
                    print(f"      Config: {result['yaml']}")
    
    # Return success only if all downloads succeeded
    all_ok = all(r["ok"] for category_results in results.values() for r in category_results)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
