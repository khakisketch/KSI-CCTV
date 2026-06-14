"""실 ONNX / 실 VLM 종단 통합 테스트 (Phase B — 'green ≠ 작동' 게이트).

기존 통합 테스트는 ONNX 추론(session.run)과 VLM(executor)을 mock으로 우회하므로
"테스트는 green인데 실제로는 안 도는" 결함을 잡지 못한다. 이 모듈은 실제 .onnx
모델과 실제 영상으로 영상→탐지→정책→이벤트 경로를 mock 없이 한 번 통과시키고,
Ollama가 가용하면 실 VLM 호출까지 검증한다.

모델 파일/영상/Ollama가 없으면 graceful skip한다(opt-in 게이트). CI/로컬에
실 자산이 있으면 자동으로 실제 경로를 검증한다.
"""
import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

try:
    import cv2
    import numpy as np  # noqa: F401
    import onnxruntime  # noqa: F401

    HAS_RUNTIME_DEPS = True
except ImportError:
    HAS_RUNTIME_DEPS = False

_ROOT = Path(__file__).parent.parent
_MODEL_PROFILES = _ROOT / "config" / "model_profiles.yaml"
_EVENT_POLICIES = _ROOT / "config" / "event_policies.yaml"
_SAMPLE = _ROOT / "data" / "sample.mp4"
_REQUIRED_MODELS = [
    _ROOT / "models" / "yolo26.onnx",
    _ROOT / "models" / "yolo26_ppe.onnx",
    _ROOT / "models" / "yolo26_fire_smoke.onnx",
]
_HAS_REAL_ASSETS = (
    HAS_RUNTIME_DEPS
    and _SAMPLE.exists()
    and all(m.exists() for m in _REQUIRED_MODELS)
)
_ASSETS_REASON = "실 ONNX 모델/샘플 영상 필요 (models/*.onnx, data/sample.mp4)"
# 실 VLM 테스트는 느리고(모델당 수십 초) 환경 의존적이라 명시 opt-in일 때만 실행한다.
_VLM_OPT_IN = os.environ.get("KSI_CCTV_TEST_VLM", "").strip() == "1"

_VALID_EVENT_TYPES = {
    "person_detected", "no_helmet", "no_vest", "fire", "smoke", "fall",
    "restricted_area", "fight", "crowding",
}


def _load_frames(limit: int = 30):
    cap = cv2.VideoCapture(str(_SAMPLE))
    frames = []
    ok, frame = cap.read()
    while ok and len(frames) < limit:
        frames.append(frame)
        ok, frame = cap.read()
    cap.release()
    return frames


@unittest.skipUnless(_HAS_REAL_ASSETS, _ASSETS_REASON)
class TestRealOnnxJourney(unittest.TestCase):
    """실 모델 + 실 영상으로 영상→탐지→정책→이벤트 종단을 mock 없이 검증."""

    @classmethod
    def setUpClass(cls):
        from src.detection.model_registry import MultiModelRegistry
        from src.policy.event_policy_engine import EventPolicyEngine

        cls.registry = MultiModelRegistry.from_config(str(_MODEL_PROFILES))
        cls.engine = EventPolicyEngine.from_config(str(_EVENT_POLICIES))
        cls.frames = _load_frames(30)
        assert cls.frames, "sample.mp4에서 프레임을 읽지 못함"

    def test_all_models_loaded(self):
        # 실제 ONNX 세션이 모두 로드됐는지 (mock 아님)
        self.assertEqual(self.registry.failed_models, {})
        self.assertEqual(
            set(self.registry.active_model_names),
            {"general_yolo", "ppe_yolo", "fire_smoke_yolo"},
        )

    def test_real_inference_class_names_are_mapped(self):
        """실 추론 결과 클래스명이 표준명으로 매핑되고 'class_N' 오라벨이 없어야 한다."""
        seen = set()
        for frame in self.frames[:8]:
            for dets in self.registry.detect_all(frame).values():
                for d in dets:
                    seen.add(d.class_name)
        # 클래스 정합성 회귀 방지: 미매핑 인덱스 이름('class_0' 등)이 없어야 함
        self.assertFalse(
            [n for n in seen if n.startswith("class_")],
            f"미매핑 클래스명 발견: {sorted(seen)}",
        )

    def test_full_journey_emits_real_events(self):
        """실 ONNX → 정책 → SafetyPipeline 콜백까지 mock 없이 통과, 실제 이벤트 발생."""
        from src.pipeline.pipeline import SafetyPipeline

        pipe = SafetyPipeline(model_registry=self.registry, policy_engine=self.engine)
        pipe.set_camera_profile("cam", {"domain": "construction"})
        fired = []
        pipe.add_alert_callback(fired.append)

        for frame in self.frames[:25]:
            pipe._process_frame_with_policy("cam", frame, datetime.now())
            if fired:
                break

        self.assertGreaterEqual(len(fired), 1, "실 종단 경로에서 이벤트가 발생하지 않음")
        for event in fired:
            self.assertIn(event.event_type, _VALID_EVENT_TYPES)
            self.assertTrue(event.source_models, "트리거 모델이 기록되지 않음")


@unittest.skipUnless(
    _HAS_REAL_ASSETS and _VLM_OPT_IN,
    "실 VLM 테스트는 KSI_CCTV_TEST_VLM=1 + 실 자산 필요 (느림)",
)
class TestRealVlmJourney(unittest.TestCase):
    """Ollama가 가용하면 실 VLM 호출을 검증(미가용/실패 시 skip — false green 방지)."""

    @classmethod
    def setUpClass(cls):
        from src.utils.config import ConfigLoader
        from src.vlm.ollama_provider import OllamaProvider

        vlm_cfg = {}
        try:
            profiles = ConfigLoader.load(str(_MODEL_PROFILES))
            vlm_cfg = profiles.get("vlm", {}) if isinstance(profiles, dict) else {}
        except Exception:
            vlm_cfg = {}

        cls.provider = OllamaProvider(
            base_url=vlm_cfg.get("base_url", "http://localhost:11434"),
            model=vlm_cfg.get("model_name", "qwen3-vl:latest"),
            timeout_sec=float(vlm_cfg.get("timeout_sec", 60)),
            max_retries=1,
        )
        if not cls.provider.is_available():
            raise unittest.SkipTest("Ollama VLM 미가용 — 실 VLM 호출 테스트 skip")

        frames = _load_frames(1)
        assert frames, "sample.mp4에서 프레임을 읽지 못함"
        cls.frame = frames[0]

    def test_real_vlm_returns_structured_analysis(self):
        from src.vlm.context_builder import ContextBuilder

        ok, buffer = cv2.imencode(".jpg", self.frame)
        self.assertTrue(ok)
        builder = ContextBuilder("./config/prompts/construction.yaml")
        prompt = builder.build(
            detections_summary="사람 1명 감지",
            recent_events="최근 이벤트 없음",
        )
        result = self.provider.analyze(buffer.tobytes(), prompt)
        metrics = self.provider.get_metrics()
        # 호출이 실제로 실패(미응답/timeout)했으면 false green을 내지 않고 skip한다.
        # (analyze는 실패 시에도 폴백 결과를 반환하므로 메트릭으로 진짜 성공을 판별)
        if metrics["failure_count"] > 0:
            self.skipTest(f"Ollama 호출 실패(미응답/timeout): {metrics['last_error']}")
        # 진짜 성공한 경우에만 구조 검증
        self.assertGreaterEqual(metrics["success_count"], 1)
        self.assertIn(result.risk_level, {"low", "medium", "high", "critical"})
        self.assertTrue(result.raw_response, "성공 응답인데 raw_response가 비어 있음")


if __name__ == "__main__":
    unittest.main()
