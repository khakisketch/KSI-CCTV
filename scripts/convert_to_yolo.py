#!/usr/bin/env python3
"""Convert various dataset formats to YOLO format for KSI-CCTV training.

Phase 8 Track B: 다운로드된 데이터셋의 다양한 라벨 형식을 YOLO 형식으로 변환합니다.

Supported input formats:
- Pascal VOC (XML)
- COCO (JSON)
- YOLO (already in format, just validate)
- CSV (custom format)
- Labelme (JSON)

YOLO output format:
- One .txt file per image
- Each line: <class_id> <x_center> <y_center> <width> <height> (normalized 0-1)
- classes.txt or dataset.yaml for class names

Usage:
    python scripts/convert_to_yolo.py --input-dir data/ppe/hard_hat_workers --format voc
    python scripts/convert_to_yolo.py --input-dir data/fire_smoke/fire_detection --format coco
    python scripts/convert_to_yolo.py --input-dir data/ppe/construction_safety --format auto
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _discover_format(input_dir: Path) -> str | None:
    """Auto-discover dataset format from directory contents."""
    xml_files = list(input_dir.rglob("*.xml"))
    json_files = list(input_dir.rglob("*.json"))
    csv_files = list(input_dir.rglob("*.csv"))
    txt_files = list(input_dir.rglob("*.txt"))

    # Check for COCO format (has images and annotations.json)
    if any("annotations" in f.name.lower() for f in json_files):
        return "coco"

    # Check for Pascal VOC (XML files alongside images)
    if xml_files:
        return "voc"

    # Check for CSV
    if csv_files:
        return "csv"

    # Check for Labelme (JSON files with shapes)
    if json_files:
        sample = json_files[0]
        try:
            with open(sample) as f:
                data = json.load(f)
            if "shapes" in data:
                return "labelme"
        except Exception:
            pass

    # Check if already YOLO format
    if txt_files:
        return "yolo"

    return None


class VOCConverter:
    """Convert Pascal VOC XML annotations to YOLO format."""

    @staticmethod
    def parse_xml(xml_path: Path) -> list[dict[str, any]]:
        """Parse a single VOC XML file."""
        tree = ET.parse(xml_path)
        root = tree.getroot()

        size = root.find("size")
        img_width = int(size.find("width").text)
        img_height = int(size.find("height").text)

        objects = []
        for obj in root.findall("object"):
            class_name = obj.find("name").text
            bbox = obj.find("bndbox")
            xmin = int(bbox.find("xmin").text)
            ymin = int(bbox.find("ymin").text)
            xmax = int(bbox.find("xmax").text)
            ymax = int(bbox.find("ymax").text)

            objects.append({
                "class_name": class_name,
                "bbox": (xmin, ymin, xmax, ymax),
                "img_size": (img_width, img_height),
            })

        return objects

    @staticmethod
    def convert(input_dir: Path, output_dir: Path, class_map: dict[str, int] | None = None) -> dict[str, int]:
        """Convert all VOC annotations in input_dir to YOLO format in output_dir."""
        _ensure_dir(output_dir / "images")
        _ensure_dir(output_dir / "labels")

        xml_files = list(input_dir.rglob("*.xml"))
        if not xml_files:
            logger.warning("No XML files found in %s", input_dir)
            return {}

        # Discover classes
        all_classes = set()
        for xml_file in xml_files:
            objects = VOCConverter.parse_xml(xml_file)
            for obj in objects:
                all_classes.add(obj["class_name"])

        if class_map is None:
            class_map = {cls: idx for idx, cls in enumerate(sorted(all_classes))}

        converted = 0
        for xml_file in xml_files:
            objects = VOCConverter.parse_xml(xml_file)
            if not objects:
                continue

            # Find corresponding image
            img_path = None
            for ext in [".jpg", ".jpeg", ".png", ".bmp"]:
                candidate = xml_file.with_suffix(ext)
                if candidate.exists():
                    img_path = candidate
                    break

            if img_path is None:
                logger.warning("No image found for %s", xml_file)
                continue

            # Copy image
            dest_img = output_dir / "images" / img_path.name
            shutil.copy2(img_path, dest_img)

            # Write YOLO annotation
            label_path = output_dir / "labels" / f"{img_path.stem}.txt"
            with open(label_path, "w") as f:
                for obj in objects:
                    class_id = class_map.get(obj["class_name"], 0)
                    img_w, img_h = obj["img_size"]
                    xmin, ymin, xmax, ymax = obj["bbox"]

                    # Convert to YOLO format (normalized center x, y, width, height)
                    x_center = (xmin + xmax) / 2.0 / img_w
                    y_center = (ymin + ymax) / 2.0 / img_h
                    width = (xmax - xmin) / img_w
                    height = (ymax - ymin) / img_h

                    f.write(f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")

            converted += 1

        logger.info("Converted %d VOC annotations to YOLO format", converted)
        return class_map


class COCOConverter:
    """Convert COCO JSON annotations to YOLO format."""

    @staticmethod
    def convert(input_dir: Path, output_dir: Path, class_map: dict[str, int] | None = None) -> dict[str, int]:
        """Convert COCO annotations to YOLO format."""
        _ensure_dir(output_dir / "images")
        _ensure_dir(output_dir / "labels")

        # Find annotations file
        json_files = list(input_dir.rglob("*.json"))
        annotations_file = None
        for f in json_files:
            if "annotations" in f.name.lower() or "coco" in f.name.lower():
                annotations_file = f
                break

        if annotations_file is None:
            logger.warning("No COCO annotations file found in %s", input_dir)
            return {}

        with open(annotations_file) as f:
            coco_data = json.load(f)

        # Build class mapping
        categories = coco_data.get("categories", [])
        if class_map is None:
            class_map = {cat["name"]: cat["id"] for cat in categories}

        # Build image info map
        images = {img["id"]: img for img in coco_data.get("images", [])}

        # Group annotations by image
        annotations_by_image: dict[int, list[dict]] = {}
        for ann in coco_data.get("annotations", []):
            img_id = ann["image_id"]
            if img_id not in annotations_by_image:
                annotations_by_image[img_id] = []
            annotations_by_image[img_id].append(ann)

        converted = 0
        for img_id, img_info in images.items():
            img_name = img_info["file_name"]
            img_width = img_info["width"]
            img_height = img_info["height"]

            # Find image file
            img_path = None
            for ext in ["", ".jpg", ".jpeg", ".png"]:
                candidates = list(input_dir.rglob(f"{Path(img_name).stem}{ext}"))
                if candidates:
                    img_path = candidates[0]
                    break

            if img_path is None:
                logger.warning("Image not found: %s", img_name)
                continue

            # Copy image
            dest_img = output_dir / "images" / img_path.name
            shutil.copy2(img_path, dest_img)

            # Write YOLO annotation
            label_path = output_dir / "labels" / f"{Path(img_name).stem}.txt"
            with open(label_path, "w") as f:
                for ann in annotations_by_image.get(img_id, []):
                    cat_id = ann["category_id"]
                    bbox = ann["bbox"]  # COCO: [x, y, width, height]

                    x, y, w, h = bbox
                    x_center = (x + w / 2.0) / img_width
                    y_center = (y + h / 2.0) / img_height
                    width = w / img_width
                    height = h / img_height

                    f.write(f"{cat_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")

            converted += 1

        logger.info("Converted %d COCO images to YOLO format", converted)
        return class_map


class CSVConverter:
    """Convert CSV annotations to YOLO format."""

    @staticmethod
    def convert(input_dir: Path, output_dir: Path, class_map: dict[str, int] | None = None) -> dict[str, int]:
        """Convert CSV annotations to YOLO format."""
        _ensure_dir(output_dir / "images")
        _ensure_dir(output_dir / "labels")

        csv_files = list(input_dir.rglob("*.csv"))
        if not csv_files:
            logger.warning("No CSV files found in %s", input_dir)
            return {}

        # Read all CSV files
        all_annotations = []
        for csv_file in csv_files:
            with open(csv_file, newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    all_annotations.append(row)

        if not all_annotations:
            logger.warning("No annotations found in CSV files")
            return {}

        # Discover classes
        all_classes = set()
        for ann in all_annotations:
            class_name = ann.get("class", ann.get("label", ann.get("class_name", "")))
            if class_name:
                all_classes.add(class_name)

        if class_map is None:
            class_map = {cls: idx for idx, cls in enumerate(sorted(all_classes))}

        # Group annotations by image
        annotations_by_image: dict[str, list[dict]] = {}
        for ann in all_annotations:
            img_name = ann.get("image", ann.get("filename", ann.get("image_name", "")))
            if img_name:
                if img_name not in annotations_by_image:
                    annotations_by_image[img_name] = []
                annotations_by_image[img_name].append(ann)

        converted = 0
        for img_name, anns in annotations_by_image.items():
            # Find image
            img_path = None
            for ext in ["", ".jpg", ".jpeg", ".png"]:
                candidates = list(input_dir.rglob(f"{Path(img_name).stem}{ext}"))
                if candidates:
                    img_path = candidates[0]
                    break

            if img_path is None:
                logger.warning("Image not found: %s", img_name)
                continue

            # Copy image
            dest_img = output_dir / "images" / img_path.name
            shutil.copy2(img_path, dest_img)

            # Write YOLO annotation
            label_path = output_dir / "labels" / f"{Path(img_name).stem}.txt"
            with open(label_path, "w") as f:
                for ann in anns:
                    class_name = ann.get("class", ann.get("label", ann.get("class_name", "")))
                    class_id = class_map.get(class_name, 0)

                    # Parse bbox (support various column names)
                    x = float(ann.get("x", ann.get("xmin", 0)))
                    y = float(ann.get("y", ann.get("ymin", 0)))
                    w = float(ann.get("width", ann.get("w", 0)))
                    h = float(ann.get("height", ann.get("h", 0)))
                    img_w = float(ann.get("img_width", ann.get("image_width", 1)))
                    img_h = float(ann.get("img_height", ann.get("image_height", 1)))

                    if img_w <= 0 or img_h <= 0:
                        continue

                    x_center = (x + w / 2.0) / img_w
                    y_center = (y + h / 2.0) / img_h
                    width = w / img_w
                    height = h / img_h

                    f.write(f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")

            converted += 1

        logger.info("Converted %d CSV annotations to YOLO format", converted)
        return class_map


class LabelmeConverter:
    """Convert Labelme JSON annotations to YOLO format."""

    @staticmethod
    def convert(input_dir: Path, output_dir: Path, class_map: dict[str, int] | None = None) -> dict[str, int]:
        """Convert Labelme annotations to YOLO format."""
        _ensure_dir(output_dir / "images")
        _ensure_dir(output_dir / "labels")

        json_files = [f for f in input_dir.rglob("*.json") if not f.name.endswith("dataset.json")]
        if not json_files:
            logger.warning("No Labelme JSON files found in %s", input_dir)
            return {}

        # Discover classes
        all_classes = set()
        for json_file in json_files:
            with open(json_file) as f:
                data = json.load(f)
            for shape in data.get("shapes", []):
                all_classes.add(shape["label"])

        if class_map is None:
            class_map = {cls: idx for idx, cls in enumerate(sorted(all_classes))}

        converted = 0
        for json_file in json_files:
            with open(json_file) as f:
                data = json.load(f)

            img_path = input_dir / data["imagePath"]
            if not img_path.exists():
                # Try to find image relative to JSON
                img_path = json_file.parent / data["imagePath"]

            if not img_path.exists():
                logger.warning("Image not found for %s: %s", json_file, data["imagePath"])
                continue

            img_width = data["imageWidth"]
            img_height = data["imageHeight"]

            # Copy image
            dest_img = output_dir / "images" / img_path.name
            shutil.copy2(img_path, dest_img)

            # Write YOLO annotation
            label_path = output_dir / "labels" / f"{img_path.stem}.txt"
            with open(label_path, "w") as f:
                for shape in data.get("shapes", []):
                    class_name = shape["label"]
                    class_id = class_map.get(class_name, 0)

                    points = shape["points"]
                    if shape["shape_type"] == "rectangle":
                        x1, y1 = points[0]
                        x2, y2 = points[1]
                    else:
                        # Convert polygon to bbox
                        xs = [p[0] for p in points]
                        ys = [p[1] for p in points]
                        x1, x2 = min(xs), max(xs)
                        y1, y2 = min(ys), max(ys)

                    x_center = (x1 + x2) / 2.0 / img_width
                    y_center = (y1 + y2) / 2.0 / img_height
                    width = (x2 - x1) / img_width
                    height = (y2 - y1) / img_height

                    f.write(f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}\n")

            converted += 1

        logger.info("Converted %d Labelme annotations to YOLO format", converted)
        return class_map


class YOLOValidator:
    """Validate that a directory is already in YOLO format."""

    @staticmethod
    def validate(input_dir: Path, output_dir: Path) -> bool:
        """Validate YOLO format and copy to output directory."""
        images_dir = input_dir / "images"
        labels_dir = input_dir / "labels"

        if not images_dir.exists() or not labels_dir.exists():
            logger.warning("Directory %s is not in YOLO format (missing images/ or labels/)", input_dir)
            return False

        # Copy to output
        if input_dir != output_dir:
            _ensure_dir(output_dir / "images")
            _ensure_dir(output_dir / "labels")

            for img in images_dir.iterdir():
                if img.is_file():
                    shutil.copy2(img, output_dir / "images" / img.name)

            for lbl in labels_dir.iterdir():
                if lbl.is_file():
                    shutil.copy2(lbl, output_dir / "labels" / lbl.name)

        logger.info("YOLO format validated and copied: %s", output_dir)
        return True


def convert_to_yolo(
    input_dir: Path,
    output_dir: Path,
    input_format: str | None = None,
    class_map: dict[str, int] | None = None,
) -> dict[str, int]:
    """Main conversion function."""
    _ensure_dir(output_dir)

    # Auto-detect format if not specified
    if input_format is None or input_format == "auto":
        input_format = _discover_format(input_dir)
        if input_format is None:
            logger.error("Could not auto-detect format for %s", input_dir)
            return {}
        logger.info("Auto-detected format: %s", input_format)

    # Convert based on format
    if input_format == "voc":
        return VOCConverter.convert(input_dir, output_dir, class_map)
    elif input_format == "coco":
        return COCOConverter.convert(input_dir, output_dir, class_map)
    elif input_format == "csv":
        return CSVConverter.convert(input_dir, output_dir, class_map)
    elif input_format == "labelme":
        return LabelmeConverter.convert(input_dir, output_dir, class_map)
    elif input_format == "yolo":
        YOLOValidator.validate(input_dir, output_dir)
        return class_map or {}
    else:
        logger.error("Unsupported format: %s", input_format)
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert datasets to YOLO format")
    parser.add_argument("--input-dir", required=True, type=Path, help="Input dataset directory")
    parser.add_argument("--output-dir", type=Path, help="Output directory (default: input-dir/yolo)")
    parser.add_argument("--format", choices=["auto", "voc", "coco", "csv", "labelme", "yolo"], default="auto", help="Input format")
    parser.add_argument("--class-map", help="JSON file with class name to ID mapping")
    parser.add_argument("--split", nargs=3, type=float, default=[0.8, 0.1, 0.1], help="Train/val/test split ratios")
    args = parser.parse_args()

    input_dir = args.input_dir
    output_dir = args.output_dir or (input_dir / "yolo")

    # Load class map if provided
    class_map = None
    if args.class_map:
        with open(args.class_map) as f:
            class_map = json.load(f)

    # Convert
    result_map = convert_to_yolo(input_dir, output_dir, args.format, class_map)

    if result_map:
        print("\nConversion successful!")
        print(f"Output directory: {output_dir}")
        print(f"Classes: {result_map}")
        return 0
    else:
        print("\nConversion failed or no data found.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
