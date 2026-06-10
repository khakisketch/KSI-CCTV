"""Event policy engine tests."""
import sys
from pathlib import Path
sys.path.append(str(Path(__file__).parent.parent))

import unittest

try:
    import numpy as np

    from src.detection.yolo_detector import Detection
    from src.policy.event_policy_engine import EventPolicyEngine
    from src.policy.rule_evaluators import RuleEvaluatorRegistry
    HAS_RUNTIME_DEPS = True
except ModuleNotFoundError:
    HAS_RUNTIME_DEPS = False


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestRuleEvaluatorRegistry(unittest.TestCase):
    def setUp(self):
        self.rules = RuleEvaluatorRegistry()
        self.frame = np.zeros((200, 200, 3), dtype=np.uint8)

    def test_direct_detection(self):
        result = self.rules.evaluate(
            {"type": "direct_detection", "classes": ["fire"], "min_confidence": 0.4},
            {"fire_smoke_yolo": [Detection(0, "fire", 0.8, (10, 10, 40, 40))]},
            self.frame,
        )

        self.assertTrue(result["triggered"])
        self.assertEqual(result["detections"][0].class_name, "fire")

    def test_count_threshold(self):
        detections = {
            "general_yolo": [
                Detection(0, "person", 0.9, (0, 0, 10, 20)),
                Detection(0, "person", 0.8, (20, 0, 30, 20)),
            ]
        }

        result = self.rules.evaluate(
            {"type": "count_threshold", "class_name": "person", "min_count": 2},
            detections,
            self.frame,
        )

        self.assertTrue(result["triggered"])
        self.assertEqual(len(result["detections"]), 2)

    def test_count_threshold_respects_min_confidence(self):
        detections = {
            "general_yolo": [
                Detection(0, "person", 0.9, (0, 0, 10, 20)),
                Detection(0, "person", 0.2, (20, 0, 30, 20)),
            ]
        }

        result = self.rules.evaluate(
            {"type": "count_threshold", "class_name": "person", "min_count": 2, "min_confidence": 0.5},
            detections,
            self.frame,
        )

        self.assertFalse(result["triggered"])

    def test_safety_gear_overlap_detects_missing_helmet(self):
        detections = {
            "ppe_yolo": [Detection(0, "person", 0.9, (0, 0, 100, 200))]
        }

        result = self.rules.evaluate(
            {"type": "safety_gear_overlap", "gear": "helmet", "person_zone": "head", "min_overlap": 0.15},
            detections,
            self.frame,
        )

        self.assertTrue(result["triggered"])
        self.assertTrue(result["ambiguous"])

    def test_safety_gear_overlap_passes_when_helmet_present(self):
        detections = {
            "ppe_yolo": [
                Detection(0, "person", 0.9, (0, 0, 100, 200)),
                Detection(1, "helmet", 0.8, (20, 5, 80, 45)),
            ]
        }

        result = self.rules.evaluate(
            {"type": "safety_gear_overlap", "gear": "helmet", "person_zone": "head", "min_overlap": 0.15},
            detections,
            self.frame,
        )

        self.assertFalse(result["triggered"])

    def test_zone_intrusion(self):
        zones = {"restricted": {"points": [[0, 0], [100, 0], [100, 100], [0, 100]]}}
        detections = {"general_yolo": [Detection(0, "person", 0.9, (10, 10, 30, 50))]}

        result = self.rules.evaluate(
            {"type": "zone_intrusion", "zone_id": "restricted"},
            detections,
            self.frame,
            zones,
        )

        self.assertTrue(result["triggered"])

    def test_proximity(self):
        detections = {
            "general_yolo": [
                Detection(0, "person", 0.9, (0, 0, 10, 20)),
                Detection(7, "truck", 0.8, (20, 0, 40, 30)),
            ]
        }

        result = self.rules.evaluate(
            {"type": "proximity", "between": ["person", ["truck"]], "max_distance_px": 50},
            detections,
            self.frame,
        )

        self.assertTrue(result["triggered"])

    def test_pose_anomaly(self):
        detections = {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 120, 40))]}

        result = self.rules.evaluate(
            {"type": "pose_anomaly", "aspect_ratio_threshold": 2.0},
            detections,
            self.frame,
        )

        self.assertTrue(result["triggered"])


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestEventPolicyEngine(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((200, 200, 3), dtype=np.uint8)
        self.policies = {
            "profiles": {
                "construction": {
                    "policies": {
                        "fire": {
                            "enabled": True,
                            "trigger": {"models": ["fire_smoke_yolo"], "classes": ["fire"], "min_confidence": 0.4},
                            "rule": {"type": "direct_detection"},
                            "vlm": "always",
                            "alert_level": "critical",
                            "cooldown_sec": 15,
                            "description": "화재 감지",
                        },
                        "crowding": {
                            "enabled": True,
                            "trigger": {"models": ["general_yolo"], "classes": ["person"]},
                            "rule": {"type": "count_threshold", "class_name": "person", "min_count": 2},
                            "vlm": "never",
                            "alert_level": "low",
                            "cooldown_sec": 60,
                        },
                    }
                }
            }
        }

    def test_policy_engine_creates_event_with_metadata(self):
        engine = EventPolicyEngine(self.policies)
        decisions = engine.evaluate(
            profile_name="construction",
            model_detections={"fire_smoke_yolo": [Detection(0, "fire", 0.9, (0, 0, 20, 20))]},
            frame=self.frame,
            camera_id="cam_001",
        )

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        self.assertEqual(decision.event.event_type, "fire")
        self.assertEqual(decision.alert_level, "critical")
        self.assertTrue(decision.vlm_needed)
        self.assertEqual(decision.source_models, ["fire_smoke_yolo"])
        self.assertEqual(decision.rule_name, "direct_detection")

    def test_policy_engine_respects_enabled_events_filter(self):
        engine = EventPolicyEngine(self.policies)
        decisions = engine.evaluate(
            profile_name="construction",
            model_detections={
                "fire_smoke_yolo": [Detection(0, "fire", 0.9, (0, 0, 20, 20))],
                "general_yolo": [
                    Detection(0, "person", 0.9, (0, 0, 10, 20)),
                    Detection(0, "person", 0.8, (30, 0, 40, 20)),
                ],
            },
            frame=self.frame,
            camera_id="cam_001",
            enabled_events=["crowding"],
        )

        self.assertEqual(len(decisions), 1)
        self.assertEqual(decisions[0].event.event_type, "crowding")
        self.assertFalse(decisions[0].vlm_needed)

    def test_project_event_policy_yaml_loads(self):
        engine = EventPolicyEngine.from_config("./config/event_policies.yaml")

        self.assertIn("construction", engine.policies.get("profiles", {}))


if __name__ == "__main__":
    unittest.main()
