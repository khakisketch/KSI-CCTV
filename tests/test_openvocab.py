"""open-vocab 탐지기(YOLO-World) 테스트 — 변환 로직(모의)·레지스트리 통합·실추론(opt-in)."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent))

try:
    import ultralytics  # noqa: F401
    _HAS_ULTRA = True
except Exception:
    _HAS_ULTRA = False


def _fake_result():
    """ultralytics 결과 모의 — boxes(xyxy/cls/conf)+names."""
    r = MagicMock()
    r.names = {0: "person", 1: "forklift"}
    r.boxes.xyxy.tolist.return_value = [[10, 20, 30, 40], [50, 60, 70, 80]]
    r.boxes.cls.tolist.return_value = [0, 1]
    r.boxes.conf.tolist.return_value = [0.9, 0.7]
    return r


@unittest.skipUnless(_HAS_ULTRA, "ultralytics 미설치 — skip")
class TestOpenVocabConversion(unittest.TestCase):
    def _detector(self):
        from src.detection.openvocab_detector import OpenVocabDetector
        fake_model = MagicMock()
        fake_model.predict.return_value = [_fake_result()]
        with patch("ultralytics.YOLOWorld", return_value=fake_model):
            det = OpenVocabDetector(model_path="x.pt", classes=["person", "forklift"])
        det._model = fake_model
        return det, fake_model

    def test_set_classes_applied(self):
        det, fake = self._detector()
        self.assertEqual(det._classes, ["person", "forklift"])
        fake.set_classes.assert_called()

    def test_detect_converts_to_detection(self):
        det, _ = self._detector()
        out = det.detect(MagicMock())  # frame mock
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0].class_name, "person")
        self.assertEqual(out[1].class_name, "forklift")  # 프롬프트명=클래스명(identity)
        self.assertEqual(out[0].bbox, (10, 20, 30, 40))
        self.assertAlmostEqual(out[0].confidence, 0.9)

    def test_detect_empty_when_no_classes(self):
        from src.detection.openvocab_detector import OpenVocabDetector
        with patch("ultralytics.YOLOWorld", return_value=MagicMock()):
            det = OpenVocabDetector(model_path="x.pt", classes=None)
        self.assertEqual(det.detect(MagicMock()), [])

    def test_set_classes_runtime_update(self):
        det, fake = self._detector()
        det.set_classes(["fire", "smoke"])
        self.assertEqual(det._classes, ["fire", "smoke"])  # 학습 없이 즉시 교체


@unittest.skipUnless(_HAS_ULTRA, "ultralytics 미설치 — skip")
class TestRegistryOpenVocab(unittest.TestCase):
    def test_open_vocab_in_supported_types(self):
        from src.detection.model_registry import MultiModelRegistry
        self.assertIn("open_vocab", MultiModelRegistry.SUPPORTED_TYPES)

    def test_create_openvocab_detector(self):
        from src.detection.model_registry import MultiModelRegistry
        reg = MultiModelRegistry.__new__(MultiModelRegistry)  # __init__ 우회(파일 로드 회피)
        fake_model = MagicMock()
        with patch("ultralytics.YOLOWorld", return_value=fake_model):
            det = reg._create_openvocab_detector({
                "model_path": "yolov8s-world.pt",
                "classes": ["person", "지게차"],
                "conf_threshold": 0.1,
            })
        from src.detection.openvocab_detector import OpenVocabDetector
        self.assertIsInstance(det, OpenVocabDetector)
        self.assertEqual(det._classes, ["person", "지게차"])


@unittest.skipUnless(
    _HAS_ULTRA and os.environ.get("KSI_CCTV_TEST_OPENVOCAB") == "1",
    "KSI_CCTV_TEST_OPENVOCAB=1 + ultralytics 필요(실 YOLO-World 추론, 느림·가중치 다운로드)",
)
class TestOpenVocabRealInference(unittest.TestCase):
    def test_text_prompt_detects_on_real_frame(self):
        """학습 없이 텍스트 프롬프트로 sample.mp4 프레임에서 탐지(제품 핵심 약속 검증)."""
        import cv2

        from src.detection.openvocab_detector import OpenVocabDetector
        sample = Path(__file__).parent.parent / "data" / "sample.mp4"
        cap = cv2.VideoCapture(str(sample))
        ok, frame = cap.read()
        cap.release()
        self.assertTrue(ok, "sample.mp4 읽기 실패")
        det = OpenVocabDetector(classes=["traffic light", "car", "person"], conf_threshold=0.05)
        out = det.detect(frame)
        # 도로 장면 → traffic light/car류가 잡혀야 함(학습 0)
        self.assertGreater(len(out), 0, "open-vocab 탐지 0건 — 프롬프트 탐지 미작동")


if __name__ == "__main__":
    unittest.main()
