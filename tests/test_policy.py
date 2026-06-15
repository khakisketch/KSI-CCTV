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

    def test_pose_anomaly_temporal_height_drop(self):
        """이전 프레임 대비 높이 급감 시 종횡비 기준 미달이어도 fall 후보로 탐지한다."""
        registry = RuleEvaluatorRegistry()
        rule = {
            "type": "pose_anomaly",
            "aspect_ratio_threshold": 2.0,
            "height_drop_ratio": 0.45,
            "temporal": True,
        }
        frame = np.zeros((300, 300, 3), dtype=np.uint8)
        # 1프레임: 서 있는 사람 (height=100, 종횡비 정상)
        d1 = Detection(0, "person", 0.9, (50, 50, 80, 150))  # w=30, h=100
        registry.evaluate(rule, {"g": [d1]}, frame, camera_id="cam_t1")

        # 2프레임: 쓰러진 사람 (height=40으로 급감, 종횡비는 2.0 미만)
        d2 = Detection(0, "person", 0.9, (50, 130, 110, 170))  # w=60, h=40 → ratio=1.5
        result = registry.evaluate(rule, {"g": [d2]}, frame, camera_id="cam_t1")

        # 높이 (100→40) 감소율 0.60 >= 0.45 → temporal 조건으로 트리거
        self.assertTrue(result["triggered"], "높이 급감 temporal 조건으로 fall 탐지해야 한다")

    def test_pose_anomaly_temporal_no_false_positive_for_stable_person(self):
        """안정적 자세(높이 변화 없음)에서는 temporal 조건이 트리거되지 않는다."""
        registry = RuleEvaluatorRegistry()
        rule = {
            "type": "pose_anomaly",
            "aspect_ratio_threshold": 2.0,
            "height_drop_ratio": 0.45,
            "temporal": True,
        }
        frame = np.zeros((300, 300, 3), dtype=np.uint8)
        d1 = Detection(0, "person", 0.9, (50, 50, 80, 150))  # h=100
        registry.evaluate(rule, {"g": [d1]}, frame, camera_id="cam_stable")

        # 동일 높이 유지 → 오탐 없어야 함
        d2 = Detection(0, "person", 0.9, (55, 55, 85, 155))  # h=100
        result = registry.evaluate(rule, {"g": [d2]}, frame, camera_id="cam_stable")

        self.assertFalse(result["triggered"], "안정 자세에서 오탐하면 안 된다")

    def test_pose_anomaly_no_false_positive_for_different_person_after_gap(self):
        """사람이 사라진 뒤 다른 위치의 새(작은) 사람이 등장해도 fall 오탐하지 않는다.

        이전 대표 height와 무관한 객체를 비교하던 stale-state 오탐(reviewer 지적)에 대한
        회귀 가드 — 위치 게이트로 '같은 객체'일 때만 temporal 비교해야 한다.
        """
        registry = RuleEvaluatorRegistry()
        rule = {
            "type": "pose_anomaly",
            "aspect_ratio_threshold": 2.0,
            "height_drop_ratio": 0.45,
            "temporal": True,
        }
        frame = np.zeros((300, 300, 3), dtype=np.uint8)
        # 1프레임: 키 큰 사람 (h=100), 화면 좌상단
        tall = Detection(0, "person", 0.9, (50, 50, 80, 150))  # center (65,100)
        registry.evaluate(rule, {"g": [tall]}, frame, camera_id="cam_gap")
        # 2프레임: 사람 없음 (상태가 stale하게 남음)
        registry.evaluate(rule, {"g": []}, frame, camera_id="cam_gap")
        # 3프레임: 화면 우하단의 전혀 다른 작은 사람 (h=40) — 같은 객체 아님
        small_far = Detection(0, "person", 0.9, (200, 200, 240, 240))  # center (220,220)
        result = registry.evaluate(rule, {"g": [small_far]}, frame, camera_id="cam_gap")

        self.assertFalse(
            result["triggered"],
            "멀리 떨어진 다른 사람을 이전 대표 height와 비교해 fall 오탐하면 안 된다",
        )

    def test_unknown_rule_type_logs_warning(self):
        """미등록 rule type은 triggered=False를 반환하고 로그 경고를 남긴다."""
        import logging

        registry = RuleEvaluatorRegistry()
        with self.assertLogs("src.policy.rule_evaluators", level=logging.WARNING) as cm:
            result = registry.evaluate(
                {"type": "nonexistent_rule"},
                {},
                self.frame,
                camera_id="cam_warn",
            )

        self.assertFalse(result["triggered"])
        self.assertTrue(any("nonexistent_rule" in msg for msg in cm.output))

    def test_proximity_self_pair_guard(self):
        """proximity에서 left_cls == right_cls일 때 자기 자신(동일 bbox)과 페어링되지 않는다."""
        # 단일 person — self-pair로 거리 0이 되면 오탐
        single_person = Detection(0, "person", 0.9, (50, 50, 100, 150))
        result = self.rules.evaluate(
            {"type": "proximity", "between": ["person", ["person"]], "max_distance_px": 200},
            {"g": [single_person]},
            self.frame,
        )
        self.assertFalse(result["triggered"], "단일 person을 자기 자신과 pair하면 안 된다")

    def test_proximity_same_class_two_objects(self):
        """proximity에서 같은 클래스라도 서로 다른 두 객체는 정상 페어링된다."""
        p1 = Detection(0, "person", 0.9, (0, 0, 30, 80))
        p2 = Detection(1, "person", 0.9, (50, 0, 80, 80))
        result = self.rules.evaluate(
            {"type": "proximity", "between": ["person", ["person"]], "max_distance_px": 200},
            {"g": [p1, p2]},
            self.frame,
        )
        self.assertTrue(result["triggered"], "서로 다른 두 person은 proximity로 탐지되어야 한다")


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

    def test_enabled_events_empty_list_allows_nothing(self):
        # 빈 리스트는 "아무 이벤트도 허용 안 함"이어야 한다(논리 반전 버그 회귀 방지).
        engine = EventPolicyEngine(self.policies)
        decisions = engine.evaluate(
            profile_name="construction",
            model_detections={"fire_smoke_yolo": [Detection(0, "fire", 0.9, (0, 0, 20, 20))]},
            frame=self.frame,
            camera_id="cam_001",
            enabled_events=[],
        )
        self.assertEqual(decisions, [])

    def test_rule_evaluation_scoped_to_trigger_models(self):
        # crowding(trigger=general_yolo, person>=2)은 general_yolo 범위만 세야 한다.
        # 다른 모델(fire_smoke_yolo)이 우연히 'person'을 내도 합산되면 안 된다(중복 카운트 방지).
        engine = EventPolicyEngine(self.policies)
        decisions = engine.evaluate(
            profile_name="construction",
            model_detections={
                "general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))],
                "fire_smoke_yolo": [Detection(0, "person", 0.9, (30, 0, 40, 20))],
            },
            frame=self.frame,
            camera_id="cam_001",
            enabled_events=["crowding"],
        )
        # general_yolo에는 person 1명뿐 → min_count=2 미달 → 미발생
        # (스코핑이 없으면 flatten으로 2명이 되어 잘못 트리거됨)
        self.assertEqual(decisions, [])

    def test_project_event_policy_yaml_loads(self):
        engine = EventPolicyEngine.from_config("./config/event_policies.yaml")

        self.assertIn("construction", engine.policies.get("profiles", {}))

    def test_unknown_profile_logs_warning_and_returns_empty(self):
        """미정의 profile은 경고 로그를 남기고 빈 리스트를 반환한다."""
        import logging

        engine = EventPolicyEngine(self.policies)
        with self.assertLogs("src.policy.event_policy_engine", level=logging.WARNING) as cm:
            decisions = engine.evaluate(
                profile_name="factory",  # policies에 없는 profile
                model_detections={"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]},
                frame=self.frame,
                camera_id="cam_factory_01",
            )

        self.assertEqual(decisions, [], "미정의 profile이면 이벤트가 발생하지 않아야 한다")
        self.assertTrue(any("factory" in msg for msg in cm.output), "profile명이 경고 로그에 포함되어야 한다")

    def test_unknown_profile_warning_emitted_only_once(self):
        """미정의 profile 경고는 세션당 1회만 발생한다(로그 스팸 방지)."""
        import logging

        engine = EventPolicyEngine(self.policies)
        # 첫 번째 호출 — 경고 발생
        with self.assertLogs("src.policy.event_policy_engine", level=logging.WARNING):
            engine.evaluate(
                profile_name="parking",
                model_detections={},
                frame=self.frame,
                camera_id="cam_p1",
            )
        # 두 번째 호출 — 경고 없어야 함 (assertLogs는 로그가 없으면 AssertionError)
        with self.assertRaises(AssertionError), self.assertLogs("src.policy.event_policy_engine", level=logging.WARNING):
            engine.evaluate(
                profile_name="parking",
                model_detections={},
                frame=self.frame,
                camera_id="cam_p2",
            )

    def test_pose_anomaly_camera_id_state_isolated(self):
        """다른 camera_id의 pose_anomaly 상태가 서로 격리된다."""
        engine = EventPolicyEngine(self.policies)
        # cam_A에서 서 있는 사람 등록
        tall_person = Detection(0, "person", 0.9, (0, 0, 30, 100))
        # cam_A에는 fall 정책이 없으므로 RuleEvaluatorRegistry를 직접 사용
        registry = engine.rule_evaluators
        rule = {"type": "pose_anomaly", "aspect_ratio_threshold": 2.0, "height_drop_ratio": 0.45}
        frame = self.frame
        registry.evaluate(rule, {"g": [tall_person]}, frame, camera_id="cam_A")

        # cam_B에는 첫 프레임이므로 이전 상태 없음 → 종횡비 기준만 판단
        short_person = Detection(0, "person", 0.9, (0, 0, 30, 40))  # h=40, 아직 초기 상태
        result = registry.evaluate(rule, {"g": [short_person]}, frame, camera_id="cam_B")
        # cam_B는 이전 상태 없음 → 종횡비(0.75) 기준도 미달 → 미탐지
        self.assertFalse(result["triggered"], "cam_B는 이전 상태가 없으므로 미탐지여야 한다")


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestPolicyEmptyPoliciesGuard(unittest.TestCase):
    """회귀 가드(#2): profile은 등록됐으나 policies가 비어있으면 경고+빈 결과."""

    def test_empty_policies_warns_and_returns_empty(self):
        import logging

        from src.policy.event_policy_engine import EventPolicyEngine

        # profile은 존재하나 policies 키가 없음 — 이전 'if not profile' 가드는 이 경우를 놓쳐
        # 모든 이벤트가 조용히 사라졌다.
        engine = EventPolicyEngine({"profiles": {"factory": {"domain": "factory"}}})
        frame = np.zeros((10, 10, 3), dtype=np.uint8)

        with self.assertLogs("src.policy.event_policy_engine", level=logging.WARNING) as cm:
            decisions = engine.evaluate("factory", {}, frame, camera_id="cam_empty")

        self.assertEqual(decisions, [])
        self.assertTrue(
            any("평가 가능한 정책이 없" in m for m in cm.output),
            "policies가 비어있으면 운영 가시성 경고가 떠야 한다",
        )
