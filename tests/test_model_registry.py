"""MultiModelRegistry tests."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import tempfile
import time
import unittest

try:
    import numpy as np

    from src.detection.model_registry import ModelMetrics, MultiModelRegistry
    from src.detection.pose_models import ActionPrediction, PoseObservation
    from src.detection.yolo_detector import Detection
    from src.utils.config import ConfigLoader

    HAS_RUNTIME_DEPS = True
except ModuleNotFoundError:
    HAS_RUNTIME_DEPS = False


class FakeDetector:
    """YOLODetector 대체 테스트 더블."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.name_hint = Path(kwargs.get("model_path", "model")).stem
        if "missing" in kwargs.get("model_path", ""):
            raise FileNotFoundError(kwargs["model_path"])

    def detect(self, image):
        if "raise" in self.name_hint:
            raise RuntimeError("inference failed")
        # latency metrics가 실제로 측정·기록되는지 검증하기 위해
        # 2자리 반올림(ms) 후에도 0이 아닌 측정 가능한 시간을 소비한다.
        time.sleep(0.0015)
        return [Detection(0, f"{self.name_hint}_object", 0.9, (0, 0, 10, 10))]


class FakePoseDetector:
    def __init__(self, model_path, **kwargs):
        self.error = "missing pose asset" if "missing" in (model_path or "") else None
        self.available = self.error is None
        self.kwargs = kwargs

    def detect(self, image, timestamp, bbox):
        return [PoseObservation([(1, 2, 0.9)], bbox, timestamp, 0.9)]


class FakeActionClassifier:
    def __init__(self, model_path, class_names, **kwargs):
        self.error = "missing action asset" if "missing" in (model_path or "") else None
        self.available = self.error is None
        self.class_names = class_names
        self.kwargs = kwargs

    def classify(self, sequence):
        return ActionPrediction(self.class_names[0], 0, 1.0, [1.0])


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestMultiModelRegistry(unittest.TestCase):
    def _profiles(self):
        return {
            "models": {
                "general_yolo": {
                    "enabled": True,
                    "type": "yolo_onnx",
                    "model_path": "./models/general.onnx",
                    "class_profile": "coco",
                    "input_size": [640, 640],
                },
                "ppe_yolo": {
                    "enabled": False,
                    "type": "yolo_onnx",
                    "model_path": "./models/ppe.onnx",
                    "classes": ["person", "helmet", "vest"],
                },
                "future_pose": {
                    "enabled": True,
                    "type": "future_pose_or_yolo",
                    "model_path": "./models/pose.onnx",
                },
            }
        }

    def test_loads_enabled_supported_models_only(self):
        registry = MultiModelRegistry(self._profiles(), detector_cls=FakeDetector)

        self.assertEqual(registry.active_model_names, ["general_yolo"])
        self.assertIn("future_pose", registry.failed_models)
        self.assertFalse(registry.load_status["ppe_yolo"].loaded)

    def test_detect_all_returns_model_keyed_results(self):
        registry = MultiModelRegistry(self._profiles(), detector_cls=FakeDetector)
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        results = registry.detect_all(image)

        self.assertEqual(list(results.keys()), ["general_yolo"])
        self.assertEqual(results["general_yolo"][0].class_name, "general_object")

    def test_detect_all_applies_class_map(self):
        profiles = {
            "models": {
                "ppe_yolo": {
                    "enabled": True,
                    "type": "yolo_onnx",
                    "model_path": "./models/ppe.onnx",
                    "class_map": {"ppe_object": "helmet"},
                }
            }
        }
        registry = MultiModelRegistry(profiles, detector_cls=FakeDetector)

        results = registry.detect_all(np.zeros((20, 20, 3), dtype=np.uint8))

        self.assertEqual(results["ppe_yolo"][0].class_name, "helmet")

    def test_failed_model_does_not_crash_when_not_strict(self):
        profiles = {
            "models": {
                "bad_model": {
                    "enabled": True,
                    "type": "yolo_onnx",
                    "model_path": "./models/missing.onnx",
                }
            }
        }

        registry = MultiModelRegistry(profiles, detector_cls=FakeDetector, strict=False)

        self.assertEqual(registry.active_model_names, [])
        self.assertIn("bad_model", registry.failed_models)

    def test_strict_mode_raises_on_load_failure(self):
        profiles = {
            "models": {
                "bad_model": {
                    "enabled": True,
                    "type": "yolo_onnx",
                    "model_path": "./models/missing.onnx",
                }
            }
        }

        with self.assertRaises(FileNotFoundError):
            MultiModelRegistry(profiles, detector_cls=FakeDetector, strict=True)

    def test_require_gpu_is_forwarded_to_enabled_yolo_models(self):
        registry = MultiModelRegistry(self._profiles(), detector_cls=FakeDetector, require_gpu=True)

        self.assertTrue(registry.models["general_yolo"].kwargs["require_gpu"])

    def test_from_config_loads_yaml(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(
                "models:\n"
                "  general_yolo:\n"
                "    enabled: true\n"
                "    type: yolo_onnx\n"
                "    model_path: ./models/general.onnx\n"
            )
            path = f.name

        try:
            registry = MultiModelRegistry.from_config(path, detector_cls=FakeDetector)
            self.assertEqual(registry.active_model_names, ["general_yolo"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_detect_all_handles_inference_failure_when_not_strict(self):
        profiles = {
            "models": {
                "raise_model": {
                    "enabled": True,
                    "type": "yolo_onnx",
                    "model_path": "./models/raise.onnx",
                }
            }
        }
        registry = MultiModelRegistry(profiles, detector_cls=FakeDetector, strict=False)

        results = registry.detect_all(np.zeros((20, 20, 3), dtype=np.uint8))

        self.assertEqual(results["raise_model"], [])

    def test_project_model_profiles_yaml_is_valid(self):
        data = ConfigLoader.load("./config/model_profiles.yaml")

        self.assertIn("models", data)
        self.assertIn("general_yolo", data["models"])
        self.assertNotIn("vlm", data["models"])
        runtime = ConfigLoader.load("./config/default.yaml")["models"]["vlm"]
        self.assertIn("runtime_qualified", runtime)
        self.assertIn("queue_hard_limit", runtime)


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestModelMetrics(unittest.TestCase):
    """ModelMetrics 단위 테스트."""

    def test_initial_state(self):
        m = ModelMetrics(name="test")
        self.assertEqual(m.inference_count, 0)
        self.assertEqual(m.success_count, 0)
        self.assertEqual(m.error_count, 0)
        self.assertEqual(m.consecutive_errors, 0)
        self.assertEqual(m.avg_latency_ms, -1.0)
        self.assertEqual(m.p50_latency_ms, -1.0)
        self.assertEqual(m.p95_latency_ms, -1.0)

    def test_record_success_accumulates(self):
        m = ModelMetrics(name="test")
        m.record_success(10.0)
        m.record_success(20.0)

        self.assertEqual(m.inference_count, 2)
        self.assertEqual(m.success_count, 2)
        self.assertEqual(m.error_count, 0)
        self.assertEqual(m.consecutive_errors, 0)
        self.assertAlmostEqual(m.avg_latency_ms, 15.0)
        self.assertAlmostEqual(m.last_latency_ms, 20.0)

    def test_record_error_increments_consecutive(self):
        m = ModelMetrics(name="test")
        m.record_error()
        m.record_error()

        self.assertEqual(m.inference_count, 2)
        self.assertEqual(m.error_count, 2)
        self.assertEqual(m.consecutive_errors, 2)

    def test_success_resets_consecutive_errors(self):
        m = ModelMetrics(name="test")
        m.record_error()
        m.record_error()
        m.record_success(5.0)

        self.assertEqual(m.consecutive_errors, 0)
        self.assertEqual(m.error_count, 2)
        self.assertEqual(m.success_count, 1)

    def test_p50_latency(self):
        m = ModelMetrics(name="test")
        for v in [10.0, 20.0, 30.0]:
            m.record_success(v)
        # 정렬 후 중앙값 = 20.0
        self.assertAlmostEqual(m.p50_latency_ms, 20.0)

    def test_p95_latency(self):
        m = ModelMetrics(name="test")
        for v in range(1, 21):  # 1..20, 20개 샘플
            m.record_success(float(v))
        # p95: idx = int(20*0.95)-1 = 18 → sorted[18] = 19.0
        self.assertAlmostEqual(m.p95_latency_ms, 19.0)

    def test_to_dict_keys(self):
        m = ModelMetrics(name="test")
        m.record_success(10.0)
        d = m.to_dict()
        for key in (
            "inference_count",
            "success_count",
            "error_count",
            "consecutive_errors",
            "avg_latency_ms",
            "p50_latency_ms",
            "p95_latency_ms",
            "last_latency_ms",
        ):
            self.assertIn(key, d)


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestRegistryMetrics(unittest.TestCase):
    """MultiModelRegistry의 metrics 수집·health 갱신 통합 테스트."""

    def _make_registry(self, model_path: str) -> MultiModelRegistry:
        profiles = {
            "models": {
                "test_model": {
                    "enabled": True,
                    "type": "yolo_onnx",
                    "model_path": model_path,
                }
            }
        }
        return MultiModelRegistry(profiles, detector_cls=FakeDetector, strict=False)

    def test_detect_all_records_success_metrics(self):
        registry = self._make_registry("./models/general.onnx")
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        registry.detect_all(image)
        registry.detect_all(image)

        metrics = registry.get_model_metrics()
        self.assertIn("test_model", metrics)
        m = metrics["test_model"]
        self.assertEqual(m["inference_count"], 2)
        self.assertEqual(m["success_count"], 2)
        self.assertEqual(m["error_count"], 0)
        self.assertGreater(m["avg_latency_ms"], 0)
        self.assertGreater(m["last_latency_ms"], 0)

    def test_detect_all_records_error_metrics(self):
        registry = self._make_registry("./models/raise.onnx")
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        registry.detect_all(image)
        registry.detect_all(image)

        metrics = registry.get_model_metrics()
        m = metrics["test_model"]
        self.assertEqual(m["error_count"], 2)
        self.assertEqual(m["success_count"], 0)
        self.assertEqual(m["consecutive_errors"], 2)

    def test_consecutive_errors_updates_load_status(self):
        """연속 실패가 임계값(3회) 초과 시 load_status.error가 갱신된다."""
        registry = self._make_registry("./models/raise.onnx")
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        threshold = MultiModelRegistry.CONSECUTIVE_ERROR_THRESHOLD

        # 임계값 미만: error 필드 갱신 없음(load 시점 오류만)
        for _ in range(threshold - 1):
            registry.detect_all(image)
        # raise.onnx는 로드 성공(FakeDetector는 detect 시에만 raise)
        # → load 시점 error=""
        status_before = registry.load_status["test_model"].error
        self.assertEqual(status_before, "")

        # 임계값 도달
        registry.detect_all(image)
        status_after = registry.load_status["test_model"].error
        self.assertIn("연속 추론 실패", status_after)

    def test_to_status_dict_includes_metrics(self):
        """to_status_dict 결과에 metrics 필드가 포함된다."""
        registry = self._make_registry("./models/general.onnx")
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        registry.detect_all(image)

        status = registry.to_status_dict()
        entry = status["test_model"]
        self.assertIn("inference_count", entry)
        self.assertIn("error_count", entry)
        self.assertIn("p50_latency_ms", entry)
        self.assertIn("p95_latency_ms", entry)

    def test_get_model_metrics_returns_all_enabled_models(self):
        """비활성 모델은 metrics에 포함되지 않는다."""
        profiles = {
            "models": {
                "active": {
                    "enabled": True,
                    "type": "yolo_onnx",
                    "model_path": "./models/general.onnx",
                },
                "inactive": {
                    "enabled": False,
                    "type": "yolo_onnx",
                    "model_path": "./models/ppe.onnx",
                },
            }
        }
        registry = MultiModelRegistry(profiles, detector_cls=FakeDetector)
        metrics = registry.get_model_metrics()

        self.assertIn("active", metrics)
        self.assertNotIn("inactive", metrics)

    def test_detect_model_records_metrics(self):
        """detect_model도 latency metrics를 기록한다."""
        registry = self._make_registry("./models/general.onnx")
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        registry.detect_model("test_model", image)

        m = registry.get_model_metrics()["test_model"]
        self.assertEqual(m["success_count"], 1)
        self.assertGreater(m["last_latency_ms"], 0)

    def test_detect_model_error_records_metrics(self):
        """detect_model 실패 시 error_count가 증가한다."""
        registry = self._make_registry("./models/raise.onnx")
        image = np.zeros((20, 20, 3), dtype=np.uint8)

        with self.assertRaises(RuntimeError):
            registry.detect_model("test_model", image)

        m = registry.get_model_metrics()["test_model"]
        self.assertEqual(m["error_count"], 1)


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestBehaviorModelRegistry(unittest.TestCase):
    def _registry(self, profiles, strict=False):
        return MultiModelRegistry(
            {"models": profiles}, detector_cls=FakeDetector,
            pose_detector_cls=FakePoseDetector, stgcn_classifier_cls=FakeActionClassifier,
            strict=strict,
        )

    def test_registers_behavior_models_without_changing_detect_all(self):
        registry = self._registry({
            "yolo": {"type": "yolo_onnx", "model_path": "yolo.onnx"},
            "pose": {
                "type": "pose_onnx", "model_path": "pose.onnx", "input_size": [320, 240],
                "keypoint_count": 1, "confidence_threshold": 0.5, "output_layout": "bnc",
                "device": "cpu",
            },
            "action": {
                "type": "stgcn_onnx", "model_path": "action.onnx", "classes": ["fall"],
                "keypoint_count": 1, "sequence_length": 2, "device": "cpu",
            },
        })

        self.assertEqual(registry.active_model_names, ["yolo", "pose", "action"])
        self.assertEqual(list(registry.detect_all(np.zeros((10, 10, 3), dtype=np.uint8))), ["yolo"])
        poses = registry.detect_poses(np.zeros((10, 10, 3), dtype=np.uint8), [(0, 0, 2, 2)], "pose", 1.0)
        self.assertEqual(len(poses), 1)
        self.assertEqual(registry.classify_action([poses[0], poses[0]], "action").action, "fall")
        status = registry.to_status_dict()
        self.assertIn("inference_count", status["pose"])
        self.assertIn("action", registry.get_model_metrics())

    def test_disabled_behavior_models_are_not_loaded_or_failed(self):
        registry = self._registry({
            "pose": {"enabled": False, "type": "pose_onnx", "model_path": "missing.onnx"},
            "action": {"enabled": False, "type": "stgcn_onnx", "model_path": "missing.onnx"},
        })

        self.assertEqual(registry.active_model_names, [])
        self.assertEqual(registry.failed_models, {})
        self.assertEqual(registry.to_status_dict()["pose"]["error"], "")

    def test_enabled_missing_behavior_asset_is_load_error(self):
        registry = self._registry({
            "pose": {"type": "pose_onnx", "model_path": "missing.onnx"},
        })

        self.assertIn("pose", registry.failed_models)
        with self.assertRaises(RuntimeError):
            self._registry({"pose": {"type": "pose_onnx", "model_path": "missing.onnx"}}, strict=True)

    def test_invalid_enabled_behavior_profile_is_load_error(self):
        registry = self._registry({"action": {"type": "stgcn_onnx", "model_path": "action.onnx"}})

        self.assertIn("class_names", registry.failed_models["action"])


if __name__ == "__main__":
    unittest.main()
