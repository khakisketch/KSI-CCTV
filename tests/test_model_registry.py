"""MultiModelRegistry tests."""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import tempfile
import unittest

try:
    import numpy as np

    from src.detection.model_registry import MultiModelRegistry
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
        return [Detection(0, f"{self.name_hint}_object", 0.9, (0, 0, 10, 10))]


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


if __name__ == "__main__":
    unittest.main()
