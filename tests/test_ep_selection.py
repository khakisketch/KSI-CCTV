"""크로스플랫폼 Execution Provider 선택 로직 단위 테스트(P2).

_select_providers는 순수 함수 — 실제 가속 없이 'device 선호도 × 가용 provider' 매칭을 검증.
"""
import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    from src.detection.yolo_detector import _select_providers
    _HAS = True
except Exception:
    _HAS = False

CUDA = "CUDAExecutionProvider"
TRT = "TensorrtExecutionProvider"
DML = "DmlExecutionProvider"
COREML = "CoreMLExecutionProvider"
QNN = "QNNExecutionProvider"
CPU = "CPUExecutionProvider"


@unittest.skipUnless(_HAS, "onnxruntime 미설치 — skip")
class TestSelectProviders(unittest.TestCase):
    def test_auto_prefers_cuda_then_cpu(self):
        self.assertEqual(_select_providers("auto", [CUDA, CPU]), [CUDA, CPU])

    def test_auto_prefers_tensorrt_over_cuda(self):
        self.assertEqual(_select_providers("auto", [CUDA, TRT, CPU])[:2], [TRT, CUDA])

    def test_auto_cpu_only(self):
        self.assertEqual(_select_providers("auto", [CPU]), [CPU])

    def test_directml_selected_on_windows_gpu(self):
        self.assertEqual(_select_providers("directml", [DML, CPU]), [DML, CPU])

    def test_coreml_on_macos(self):
        self.assertEqual(_select_providers("coreml", [COREML, CPU]), [COREML, CPU])

    def test_qnn_on_windows_arm64(self):
        self.assertEqual(_select_providers("qnn", [QNN, CPU]), [QNN, CPU])

    def test_cpu_always_fallback_when_pref_unavailable(self):
        # cuda 요청했으나 CUDA 미가용 → CPU만
        self.assertEqual(_select_providers("cuda", [CPU]), [CPU])
        # directml 요청했으나 미가용 → CPU 폴백 보장
        self.assertEqual(_select_providers("directml", [CPU]), [CPU])

    def test_cpu_always_present_even_if_missing_from_available(self):
        # 가용 목록에 CPU가 없어도 최후 폴백으로 추가
        out = _select_providers("auto", [CUDA])
        self.assertIn(CPU, out)
        self.assertEqual(out[-1], CPU)

    def test_unknown_device_treated_as_auto(self):
        self.assertEqual(_select_providers("nonsense", [CUDA, CPU]), [CUDA, CPU])

    def test_case_insensitive_device(self):
        self.assertEqual(_select_providers("CUDA", [CUDA, CPU]), [CUDA, CPU])


if __name__ == "__main__":
    unittest.main()
