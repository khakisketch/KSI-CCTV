"""ONNX 메타데이터 기반 클래스 자동 인식 테스트.

근본 수정 검증: detector가 config의 classes 인덱스를 맹신하지 않고
ONNX 모델에 내장된 `names` 메타데이터를 1차 출처로 사용하는지,
config가 모델과 어긋날 때 메타데이터를 신뢰해 오라벨을 방지하는지 확인한다.
"""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    import cv2  # noqa: F401
    import onnxruntime  # noqa: F401

    HAS_RUNTIME_DEPS = True
except ImportError:
    HAS_RUNTIME_DEPS = False

if HAS_RUNTIME_DEPS:
    from src.detection.yolo_detector import YOLODetector


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requires cv2/onnxruntime")
class TestNormalizeClasses(unittest.TestCase):
    def test_list(self):
        self.assertEqual(YOLODetector._normalize_classes(["a", "b"]), {0: "a", 1: "b"})

    def test_dict(self):
        self.assertEqual(YOLODetector._normalize_classes({0: "a", 2: "c"}), {0: "a", 2: "c"})

    def test_csv_string(self):
        self.assertEqual(YOLODetector._normalize_classes("a, b ,c"), {0: "a", 1: "b", 2: "c"})

    def test_empty_returns_none(self):
        self.assertIsNone(YOLODetector._normalize_classes(None))
        self.assertIsNone(YOLODetector._normalize_classes([]))
        self.assertIsNone(YOLODetector._normalize_classes(""))


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requires cv2/onnxruntime")
class TestResolveClassesPrecedence(unittest.TestCase):
    META = {0: "Hardhat", 1: "NO-Hardhat"}

    def test_metadata_primary_when_no_explicit(self):
        self.assertEqual(
            YOLODetector._resolve_classes(None, "safety_custom", self.META), self.META
        )

    def test_explicit_honored_when_count_matches(self):
        # 개수가 메타데이터와 일치하면 의도적 override로 존중
        self.assertEqual(
            YOLODetector._resolve_classes(["helmet", "no_helmet"], "safety_custom", self.META),
            {0: "helmet", 1: "no_helmet"},
        )

    def test_metadata_wins_when_count_mismatches(self):
        # config가 잘못된 경우(개수 불일치) — 메타데이터를 신뢰해 silent 오라벨 방지
        self.assertEqual(
            YOLODetector._resolve_classes(["person", "helmet", "vest"], "safety_custom", self.META),
            self.META,
        )

    def test_explicit_used_when_no_metadata(self):
        self.assertEqual(
            YOLODetector._resolve_classes(["a", "b"], "coco", None), {0: "a", 1: "b"}
        )

    def test_coco_fallback_when_nothing_available(self):
        resolved = YOLODetector._resolve_classes(None, "coco", None)
        self.assertEqual(len(resolved), 80)
        self.assertEqual(resolved[0], "person")

    def test_safety_fallback_when_nothing_available(self):
        resolved = YOLODetector._resolve_classes(None, "safety_custom", None)
        self.assertEqual(resolved[1], "helmet")


_MODELS_DIR = Path(__file__).parent.parent / "models"
_PPE_MODEL = _MODELS_DIR / "yolo26_ppe.onnx"
_FIRE_MODEL = _MODELS_DIR / "yolo26_fire_smoke.onnx"


@unittest.skipUnless(
    HAS_RUNTIME_DEPS and _PPE_MODEL.exists() and _FIRE_MODEL.exists(),
    "requires real safety ONNX models",
)
class TestMetadataIntegration(unittest.TestCase):
    """실제 ONNX 모델로 메타데이터 자동 인식이 동작하는지 검증."""

    def test_ppe_classes_auto_detected_from_metadata(self):
        det = YOLODetector(
            model_path=str(_PPE_MODEL), classes=None, class_profile="safety_custom", device="cpu"
        )
        self.assertEqual(det.classes, {0: "Hardhat", 1: "NO-Hardhat"})

    def test_fire_classes_auto_detected_from_metadata(self):
        det = YOLODetector(
            model_path=str(_FIRE_MODEL), classes=None, class_profile="custom", device="cpu"
        )
        self.assertEqual(
            det.classes,
            {0: "fire-smoke", 1: "fog", 2: "sol", 3: "fire", 4: "factory-smoke"},
        )

    def test_wrong_config_classes_overridden_by_metadata(self):
        # 일부러 틀린 classes(3개)를 줘도 메타데이터(2개)가 이긴다 → 오라벨 방지
        det = YOLODetector(
            model_path=str(_PPE_MODEL),
            classes=["person", "helmet", "vest"],
            class_profile="safety_custom",
            device="cpu",
        )
        self.assertEqual(det.classes, {0: "Hardhat", 1: "NO-Hardhat"})


if __name__ == "__main__":
    unittest.main()
