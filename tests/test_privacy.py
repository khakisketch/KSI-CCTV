"""얼굴 블러 SSOT(src.utils.privacy) 테스트 — 저장·표시 공용 로직(#11).

blur_faces가 저장 경로에서만 적용되고 대시보드 표시에서 누락되던 PII 누출을 막기 위해
분리한 공용 유틸의 회귀 가드.
"""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    import cv2
    # 다른 테스트(test_dashboard_smoke)가 sys.modules에 주입한 cv2 스텁이면 .data가 없다 →
    # 실제 cv2일 때만 실행(스텁이면 skip — 수집 순서에 따른 false-fail 방지).
    HAS_CV2 = hasattr(cv2, "data")
except ImportError:
    HAS_CV2 = False

from src.utils import privacy


@unittest.skipUnless(HAS_NUMPY and HAS_CV2, "numpy/cv2 없음 — skip")
class TestBlurFaces(unittest.TestCase):
    def _img(self, h=120, w=160):
        return np.zeros((h, w, 3), dtype=np.uint8)

    def test_none_returns_none(self):
        self.assertIsNone(privacy.blur_faces(None))

    def test_shape_preserved(self):
        img = self._img()
        out = privacy.blur_faces(img, privacy.get_face_cascades(), strength=35)
        self.assertEqual(out.shape, img.shape)

    def test_no_cascades_returns_input_unchanged(self):
        """cascade 빈 목록이면 원본을 그대로 반환(블러 불가)."""
        img = self._img()
        out = privacy.blur_faces(img, cascades=[], strength=35)
        np.testing.assert_array_equal(out, img)

    def test_does_not_mutate_original(self):
        """블러는 copy에 적용 — 원본 배열 불변."""
        img = self._img()
        before = img.copy()
        privacy.blur_faces(img, privacy.get_face_cascades(), strength=35)
        np.testing.assert_array_equal(img, before)

    def test_get_face_cascades_cached(self):
        """프로세스당 1회 캐시 — 같은 객체 반환."""
        first = privacy.get_face_cascades()
        second = privacy.get_face_cascades()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
