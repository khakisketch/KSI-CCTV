"""class_adapter 단위 테스트 — 공개 모델 클래스명 → KSI 표준 매핑 + 미매핑 drop."""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    from src.detection.class_adapter import adapt_detections
    from src.detection.yolo_detector import Detection

    HAS_DEPS = True
except ModuleNotFoundError:
    HAS_DEPS = False


@unittest.skipUnless(HAS_DEPS, "requirements.txt 의존성 필요")
class TestAdaptDetections(unittest.TestCase):
    def _det(self, name):
        return Detection(class_id=0, class_name=name, confidence=0.9, bbox=(0, 0, 10, 10))

    def test_mapped_names_converted(self):
        cmap = {"Hardhat": "helmet", "NO-Hardhat": "no_helmet"}
        out = adapt_detections([self._det("Hardhat"), self._det("NO-Hardhat")], class_map=cmap)
        self.assertEqual([d.class_name for d in out], ["helmet", "no_helmet"])

    def test_unmapped_dropped_when_class_map_present(self):
        # PPE 모델의 Mask/machinery 등 KSI 표준 외 클래스는 결과에서 제외(정책/대시보드 noise 방지)
        cmap = {"Hardhat": "helmet", "Safety Vest": "vest"}
        out = adapt_detections(
            [self._det("Hardhat"), self._det("Mask"), self._det("machinery"), self._det("Safety Vest")],
            class_map=cmap,
        )
        self.assertEqual(sorted(d.class_name for d in out), ["helmet", "vest"])

    def test_unmapped_kept_when_drop_disabled(self):
        cmap = {"Hardhat": "helmet"}
        out = adapt_detections([self._det("Hardhat"), self._det("Mask")], class_map=cmap, drop_unmapped=False)
        self.assertEqual(sorted(d.class_name for d in out), ["Mask", "helmet"])

    def test_no_class_map_passes_all_through(self):
        # COCO general_yolo처럼 class_map이 없으면 전체 통과(person/vehicle 등 보존)
        dets = [self._det("person"), self._det("car")]
        out = adapt_detections(dets, class_map=None)
        self.assertEqual([d.class_name for d in out], ["person", "car"])


if __name__ == "__main__":
    unittest.main()
