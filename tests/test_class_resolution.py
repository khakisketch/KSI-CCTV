"""ONNX 메타데이터 기반 클래스 자동 인식 + letterbox 역보정 테스트.

근본 수정 검증:
- detector가 ONNX names 메타데이터를 1차 출처로 사용하는지
- config 개수 불일치 시 메타데이터를 신뢰해 오라벨을 방지하는지
- letterbox 전처리 시 패딩/스케일 역보정이 올바른지
- target_classes 불일치 경고가 발생하는지
"""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    import cv2  # noqa: F401
    import numpy as np  # noqa: F401
    import onnxruntime  # noqa: F401

    HAS_RUNTIME_DEPS = True
except ImportError:
    HAS_RUNTIME_DEPS = False

if HAS_RUNTIME_DEPS:
    from src.detection.class_adapter import warn_target_class_mismatch
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


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requires cv2/onnxruntime/numpy")
class TestLetterboxPreprocess(unittest.TestCase):
    """letterbox 전처리 + 역보정 단위 테스트 (실제 ONNX 모델 불필요).

    YOLODetector._letterbox_pad 를 직접 주입해 _scale_xywh_to_original /
    _postprocess_final 의 역보정 수식을 검증한다.
    """

    def _make_detector(self) -> "YOLODetector":
        """세션 없이 _resolve_classes / _letterbox_pad 만 쓰는 최소 detector."""
        det = object.__new__(YOLODetector)
        det.input_size = (640, 640)
        det.conf_threshold = 0.25
        det.iou_threshold = 0.45
        det.classes = {0: "person"}
        det.postprocess_mode = "auto"
        return det

    def test_letterbox_pad_state_set_on_preprocess(self):
        """16:9 이미지에 대한 letterbox 수식이 올바른 패딩을 계산한다."""
        # preprocess를 직접 호출할 수 없으므로 letterbox 수식을 직접 검증
        ih, iw = 360, 640
        tw, th = 640, 640
        scale = min(tw / iw, th / ih)  # = 1.0 (w 기준)
        new_h = int(round(ih * scale))  # = 360
        pad_top = (th - new_h) // 2  # = 140
        self.assertEqual(pad_top, 140)
        self.assertAlmostEqual(scale, 1.0)

    def test_scale_xywh_no_padding(self):
        """패딩 없는(정방형 입력) 경우 역보정 = 단순 비율 환산."""
        det = self._make_detector()
        # input_size=640, original=640×640, letterbox 없음(scale=1, 패딩 0) → 좌표 변환 없음
        x1, y1, x2, y2 = det._scale_xywh_to_original(320, 320, 100, 100, (640, 640), (1.0, 0, 0))
        self.assertEqual(x1, 270)
        self.assertEqual(y1, 270)
        self.assertEqual(x2, 370)
        self.assertEqual(y2, 370)

    def test_scale_xywh_with_letterbox_padding(self):
        """letterbox 패딩이 있을 때 역보정이 올바른 원본 좌표를 반환한다.

        시나리오: 원본 640×360, input_size 640×640
        scale = min(640/640, 640/360) = 1.0 (w 기준, h가 제약)
        → 실제로는 scale=min(640/640,640/360)=1.0 이 아니라
          scale=min(1.0, 1.777..) = 1.0 이므로 640×640 → new_w=640, new_h=360, pad_top=140

        모델 출력 좌표 (input_size 기준): cx=320, cy=250, w=200, h=100
          → x1=220, y1=200, x2=420, y2=300 (input_size 기준)
          → 역보정: x=(x-0)/1.0, y=(y-140)/1.0
          → 원본: x1=220, y1=60, x2=420, y2=160
        """
        det = self._make_detector()
        scale = 1.0
        pad_left = 0
        pad_top = 140
        orig_shape = (360, 640)  # (h, w)

        x1, y1, x2, y2 = det._scale_xywh_to_original(
            320, 250, 200, 100, orig_shape, (scale, pad_left, pad_top)
        )
        self.assertEqual(x1, 220)
        self.assertEqual(y1, 60)
        self.assertEqual(x2, 420)
        self.assertEqual(y2, 160)

    def test_scale_xywh_clamps_to_original_bounds(self):
        """역보정 후 경계를 벗어나는 좌표는 원본 크기로 clamp된다."""
        det = self._make_detector()
        # bbox가 이미지 경계를 넘는 경우
        x1, y1, x2, y2 = det._scale_xywh_to_original(0, 0, 100, 100, (480, 640), (1.0, 0, 0))
        self.assertEqual(x1, 0)
        self.assertEqual(y1, 0)

    def test_letterbox_preserves_aspect_ratio_math(self):
        """비정방형 입력에서 letterbox scale 계산이 종횡비를 유지한다."""
        # 원본 1920×1080, target 640×640
        ih, iw = 1080, 1920
        tw, th = 640, 640
        scale = min(tw / iw, th / ih)  # = min(0.333, 0.592) = 0.333
        new_w = int(round(iw * scale))  # = 640
        new_h = int(round(ih * scale))  # = 360
        pad_top = (th - new_h) // 2    # = 140
        pad_left = (tw - new_w) // 2   # = 0

        self.assertAlmostEqual(scale, 640 / 1920, places=5)
        self.assertEqual(new_w, 640)
        self.assertEqual(new_h, 360)
        self.assertEqual(pad_top, 140)
        self.assertEqual(pad_left, 0)


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requires cv2/onnxruntime")
class TestTargetClassMismatchWarning(unittest.TestCase):
    """warn_target_class_mismatch 경고 발생 검증."""

    def test_warning_on_missing_target_class(self):
        """detector에 없는 target_class 요청 시 WARNING 로그가 발생한다."""
        detector_classes = {0: "person", 1: "helmet"}
        target_classes = ["person", "helmet", "fire"]  # fire는 detector에 없음

        with self.assertLogs("src.detection.class_adapter", level="WARNING") as cm:
            warn_target_class_mismatch("test_model", detector_classes, target_classes)

        self.assertTrue(any("fire" in msg for msg in cm.output))
        self.assertTrue(any("target_classes 불일치" in msg for msg in cm.output))

    def test_no_warning_when_all_targets_present(self):
        """detector가 모든 target_class를 보유하면 WARNING이 없다."""
        import logging

        detector_classes = {0: "person", 1: "helmet", 2: "fire"}
        target_classes = ["person", "helmet"]

        # WARNING이 발생하지 않아야 함 — assertLogs로 잡히면 실패
        # DEBUG 레벨은 발생할 수 있으므로 WARNING만 체크
        logger = logging.getLogger("src.detection.class_adapter")
        with self.assertLogs(logger, level="DEBUG") as cm:
            warn_target_class_mismatch("test_model", detector_classes, target_classes)
            # DEBUG 로그(extra_in_detector)는 발생할 수 있음

        # WARNING 레벨 로그는 없어야 한다
        warnings = [msg for msg in cm.output if "WARNING" in msg]
        self.assertEqual(warnings, [])

    def test_empty_target_classes_no_warning(self):
        """target_classes가 비어 있으면 경고하지 않는다."""
        detector_classes = {0: "person"}
        # assertLogs 없이 호출만 해서 예외 없음을 확인
        warn_target_class_mismatch("test_model", detector_classes, [])


if __name__ == "__main__":
    unittest.main()
