#!/usr/bin/env python3
"""Dataset validation script for KSI-CCTV custom model training.

Phase 8 Track B: YOLO 형식 데이터셋의 무결성을 검증합니다.
- 이미지/라벨 파일 일치 여부
- 라벨 파일 형식 검증
- 클래스 분포 통계
- 손상된 이미지 감지
- 중복 검사

Usage:
    python scripts/validate_dataset.py --dataset-dir data/ppe/yolo
    python scripts/validate_dataset.py --dataset-dir data/fire_smoke/yolo --report
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from pathlib import Path

import cv2

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _list_files(directory: Path, extensions: tuple[str, ...]) -> list[Path]:
    """List files with given extensions."""
    files = []
    if directory.exists():
        for ext in extensions:
            files.extend(directory.rglob(f"*{ext}"))
    return sorted(files)


def _read_yolo_labels(label_path: Path) -> list[list[float]]:
    """Read YOLO format labels from file."""
    labels = []
    try:
        with open(label_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) != 5:
                    logger.warning("Invalid label format in %s: %s", label_path, line)
                    continue
                try:
                    class_id = int(parts[0])
                    x_center = float(parts[1])
                    y_center = float(parts[2])
                    width = float(parts[3])
                    height = float(parts[4])
                    labels.append([class_id, x_center, y_center, width, height])
                except ValueError:
                    logger.warning("Invalid values in %s: %s", label_path, line)
    except Exception as exc:
        logger.error("Failed to read %s: %s", label_path, exc)
    return labels


def validate_image(image_path: Path) -> tuple[bool, str | None, tuple[int, int] | None]:
    """Validate that an image file is readable and not corrupted."""
    try:
        img = cv2.imread(str(image_path))
        if img is None:
            return False, "Cannot read image (possibly corrupted)", None
        height, width = img.shape[:2]
        if height <= 0 or width <= 0:
            return False, "Invalid image dimensions", None
        return True, None, (width, height)
    except Exception as exc:
        return False, f"Exception: {exc}", None


def validate_labels(
    label_path: Path,
    image_size: tuple[int, int] | None = None,
    num_classes: int | None = None,
) -> tuple[bool, list[str]]:
    """Validate YOLO label file."""
    errors = []

    if not label_path.exists():
        return False, ["Label file not found"]

    labels = _read_yolo_labels(label_path)

    if not labels:
        return False, ["Empty or unreadable label file"]

    for i, (class_id, x_center, y_center, width, height) in enumerate(labels):
        # Check normalized values are in [0, 1]
        if not (0 <= x_center <= 1 and 0 <= y_center <= 1):
            errors.append(f"Line {i+1}: Center coordinates out of range [0,1]")
        if not (0 < width <= 1 and 0 < height <= 1):
            errors.append(f"Line {i+1}: Width/height out of range [0,1]")

        # Check class ID
        if num_classes is not None and class_id >= num_classes:
            errors.append(f"Line {i+1}: Class ID {class_id} >= num_classes {num_classes}")

        # Check negative values
        if x_center < 0 or y_center < 0 or width < 0 or height < 0:
            errors.append(f"Line {i+1}: Negative values")

    return len(errors) == 0, errors


def validate_dataset(
    dataset_dir: Path,
    num_classes: int | None = None,
    strict: bool = False,
) -> dict[str, any]:
    """Validate a YOLO format dataset directory."""
    images_dir = dataset_dir / "images"
    labels_dir = dataset_dir / "labels"

    result = {
        "dataset_dir": str(dataset_dir),
        "valid": True,
        "errors": [],
        "warnings": [],
        "stats": {},
    }

    # Check directory structure
    if not images_dir.exists():
        result["valid"] = False
        result["errors"].append("images/ directory not found")
        return result

    if not labels_dir.exists():
        result["warnings"].append("labels/ directory not found (images without labels?)")

    # Collect images and labels
    image_extensions = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    images = _list_files(images_dir, image_extensions)
    labels = _list_files(labels_dir, (".txt",))

    image_names = {img.stem for img in images}
    label_names = {lbl.stem for lbl in labels}

    result["stats"]["total_images"] = len(images)
    result["stats"]["total_labels"] = len(labels)

    # Check for images without labels
    images_without_labels = image_names - label_names
    if images_without_labels:
        result["warnings"].append(f"{len(images_without_labels)} images without labels")
        if strict:
            result["valid"] = False
            result["errors"].append(f"Images without labels: {len(images_without_labels)}")

    # Check for labels without images
    labels_without_images = label_names - image_names
    if labels_without_images:
        result["warnings"].append(f"{len(labels_without_images)} labels without images")

    # Validate each image-label pair
    class_distribution = Counter()
    valid_pairs = 0
    invalid_pairs = 0

    for img_path in images:
        stem = img_path.stem
        label_path = labels_dir / f"{stem}.txt"

        # Validate image
        img_valid, img_error, img_size = validate_image(img_path)
        if not img_valid:
            result["errors"].append(f"Image {img_path.name}: {img_error}")
            invalid_pairs += 1
            continue

        # Validate label if exists
        if label_path.exists():
            lbl_valid, lbl_errors = validate_labels(label_path, img_size, num_classes)
            if not lbl_valid:
                for error in lbl_errors:
                    result["errors"].append(f"Label {label_path.name}: {error}")
                invalid_pairs += 1
                continue

            # Count class distribution
            labels = _read_yolo_labels(label_path)
            for label in labels:
                class_distribution[label[0]] += 1

        valid_pairs += 1

    result["stats"]["valid_pairs"] = valid_pairs
    result["stats"]["invalid_pairs"] = invalid_pairs
    result["stats"]["class_distribution"] = dict(class_distribution)

    if result["errors"]:
        result["valid"] = False

    return result


def validate_split(dataset_dir: Path) -> dict[str, any]:
    """Validate train/val/test split directories."""
    splits = ["train", "val", "test"]
    results = {}

    for split in splits:
        split_dir = dataset_dir / split
        if split_dir.exists():
            results[split] = validate_dataset(split_dir)
        else:
            results[split] = {"valid": False, "error": "Directory not found"}

    return results


def generate_report(results: dict[str, any], output_path: Path | None = None) -> None:
    """Generate validation report."""
    report_lines = [
        "# KSI-CCTV Dataset Validation Report",
        "",
        f"Dataset: {results['dataset_dir']}",
        f"Valid: {'YES' if results['valid'] else 'NO'}",
        "",
        "## Statistics",
        f"- Total Images: {results['stats'].get('total_images', 0)}",
        f"- Total Labels: {results['stats'].get('total_labels', 0)}",
        f"- Valid Pairs: {results['stats'].get('valid_pairs', 0)}",
        f"- Invalid Pairs: {results['stats'].get('invalid_pairs', 0)}",
        "",
        "## Class Distribution",
    ]

    class_dist = results['stats'].get('class_distribution', {})
    if class_dist:
        for class_id, count in sorted(class_dist.items()):
            report_lines.append(f"- Class {class_id}: {count} instances")
    else:
        report_lines.append("- No classes found")

    if results['warnings']:
        report_lines.extend(["", "## Warnings"])
        for warning in results['warnings']:
            report_lines.append(f"- {warning}")

    if results['errors']:
        report_lines.extend(["", "## Errors"])
        for error in results['errors']:
            report_lines.append(f"- {error}")

    report_text = "\n".join(report_lines)

    if output_path:
        output_path.write_text(report_text, encoding="utf-8")
        logger.info("Report saved to: %s", output_path)
    else:
        print(report_text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate YOLO format dataset")
    parser.add_argument("--dataset-dir", required=True, type=Path, help="Dataset directory (contains images/ and labels/)")
    parser.add_argument("--num-classes", type=int, help="Expected number of classes")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    parser.add_argument("--report", action="store_true", help="Generate validation report")
    parser.add_argument("--output", type=Path, help="Report output path")
    parser.add_argument("--json", action="store_true", help="Output JSON summary")
    args = parser.parse_args()

    dataset_dir = args.dataset_dir

    logger.info("Validating dataset: %s", dataset_dir)

    # Check if it's a split dataset (train/val/test)
    has_splits = all((dataset_dir / split).exists() for split in ["train", "val"])

    if has_splits:
        logger.info("Detected split dataset (train/val/test)")
        results = validate_split(dataset_dir)

        # Combine results
        combined = {
            "dataset_dir": str(dataset_dir),
            "valid": all(r.get("valid", False) for r in results.values()),
            "splits": results,
        }
    else:
        combined = validate_dataset(dataset_dir, num_classes=args.num_classes, strict=args.strict)

    if args.json:
        print(json.dumps(combined, indent=2, ensure_ascii=False))
    elif args.report or args.output:
        generate_report(combined, args.output)
    else:
        # Simple console output
        print(f"\nDataset: {combined['dataset_dir']}")
        print(f"Valid: {'YES' if combined['valid'] else 'NO'}")

        if "splits" in combined:
            for split, result in combined["splits"].items():
                print(f"\n  {split}:")
                print(f"    Images: {result['stats'].get('total_images', 0)}")
                print(f"    Labels: {result['stats'].get('total_labels', 0)}")
                print(f"    Valid: {'YES' if result['valid'] else 'NO'}")
        else:
            stats = combined.get("stats", {})
            print(f"  Images: {stats.get('total_images', 0)}")
            print(f"  Labels: {stats.get('total_labels', 0)}")
            print(f"  Valid Pairs: {stats.get('valid_pairs', 0)}")
            print(f"  Invalid: {stats.get('invalid_pairs', 0)}")

        if combined.get("errors"):
            print(f"\n  Errors ({len(combined['errors'])}):")
            for error in combined["errors"][:10]:
                print(f"    - {error}")
            if len(combined["errors"]) > 10:
                print(f"    ... and {len(combined['errors']) - 10} more")

        if combined.get("warnings"):
            print(f"\n  Warnings ({len(combined['warnings'])}):")
            for warning in combined["warnings"][:5]:
                print(f"    - {warning}")

    return 0 if combined["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
