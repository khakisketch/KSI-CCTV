"""Event policy engine tests."""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import unittest
from unittest.mock import patch

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

    def test_temporal_gate_requires_min_hits(self):
        """한 프레임만 trigger되어도 min_hits 미달이면 사건으로 보고하지 않는다.

        제품 정의: '단일 프레임으로 사건을 만들지 않는다'(화재 등 즉시성 제외).
        """
        rule = {
            "type": "direct_detection",
            "classes": ["fire"],
            "min_confidence": 0.4,
            "temporal": {"min_hits": 3, "max_gap_sec": 2, "clear_after_sec": 5},
        }
        detections = {"general_yolo": [Detection(0, "fire", 0.9, (10, 10, 40, 40))]}
        # 1frame: 후보만 관측, 사건 미발화(아직 hits=1, min_hits=3 미달)
        r1 = self.rules.evaluate(rule, detections, self.frame, camera_id="cam_tg1", policy_name="fire")
        self.assertFalse(r1["triggered"], "단일 hit는 사건이 되면 안 된다")
        self.assertEqual(r1["temporal"]["hits"], 1)
        # 같은 프레임 다시 호출 — hits=2, 여전히 min_hits=3 미만
        self.rules.evaluate(rule, detections, self.frame, camera_id="cam_tg1", policy_name="fire")
        r3 = self.rules.evaluate(rule, detections, self.frame, camera_id="cam_tg1", policy_name="fire")
        self.assertTrue(r3["triggered"], "3번째 hit에서 사건 발화해야 한다")
        self.assertEqual(r3["temporal"]["hits"], 3)

    def test_temporal_gate_resets_on_gap(self):
        """max_gap_sec 이상 후보가 비면 시퀀스를 리셋한다."""
        rule = {
            "type": "direct_detection",
            "classes": ["fire"],
            "min_confidence": 0.4,
            "temporal": {"min_hits": 2, "max_gap_sec": 0.05, "clear_after_sec": 1},
        }
        dets = {"general_yolo": [Detection(0, "fire", 0.9, (10, 10, 40, 40))]}
        empty = {"general_yolo": []}
        self.rules.evaluate(rule, dets, self.frame, camera_id="cam_tg2", policy_name="fire")
        # 시간 흐름 (monotonic이 0.05초 이상 지나도록 sleep)
        import time as _t
        _t.sleep(0.12)
        self.rules.evaluate(rule, empty, self.frame, camera_id="cam_tg2", policy_name="fire")
        # 다음 hit는 이전 시퀀스와 무관 (gap 리셋 적용)
        r2 = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_tg2", policy_name="fire")
        self.assertFalse(r2["triggered"], "gap 리셋 후 단일 hit로 사건이 되면 안 된다")

    def test_temporal_gate_emit_once_per_clear(self):
        """한 클리어 윈도우 안에서 같은 사건이 두 번 발화되면 안 된다."""
        rule = {
            "type": "direct_detection",
            "classes": ["fire"],
            "min_confidence": 0.4,
            "temporal": {"min_hits": 2, "max_gap_sec": 2, "clear_after_sec": 60},
        }
        dets = {"general_yolo": [Detection(0, "fire", 0.9, (10, 10, 40, 40))]}
        # 2 hit → 발화
        self.rules.evaluate(rule, dets, self.frame, camera_id="cam_tg3", policy_name="fire")
        r1 = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_tg3", policy_name="fire")
        self.assertTrue(r1["triggered"])
        # 추가 hit가 와도 같은 clear 윈도우 안이면 다시 발화되면 안 됨
        r2 = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_tg3", policy_name="fire")
        self.assertFalse(r2["triggered"], "이미 발화한 사건은 clear 윈도우 안에서 중복 발화하면 안 된다")

    def test_temporal_gate_counts_hits_inside_frame_window(self):
        rule = {
            "type": "direct_detection",
            "classes": ["fire"],
            "temporal": {
                "min_hits": 3,
                "window_frames": 5,
                "max_gap_sec": 10,
                "clear_after_sec": 10,
            },
        }
        dets = {"g": [Detection(0, "fire", 0.9, (0, 0, 10, 10))]}
        with patch("src.policy.rule_evaluators.time.monotonic", side_effect=[0, 1, 2, 3, 4]):
            self.rules.evaluate(rule, dets, self.frame, camera_id="cam_window", policy_name="fire")
            self.rules.observe_miss(rule, "cam_window", "fire")
            self.rules.evaluate(rule, dets, self.frame, camera_id="cam_window", policy_name="fire")
            self.rules.observe_miss(rule, "cam_window", "fire")
            result = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_window", policy_name="fire")
        self.assertTrue(result["triggered"])
        self.assertEqual(result["temporal"]["hits"], 3)
        self.assertEqual(result["temporal"]["samples"], 5)

    def test_temporal_gate_does_not_refire_while_signal_remains_active(self):
        rule = {
            "type": "direct_detection",
            "classes": ["fire"],
            "temporal": {
                "min_hits": 2,
                "window_frames": 5,
                "max_gap_sec": 2,
                "clear_after_sec": 2,
            },
        }
        dets = {"g": [Detection(0, "fire", 0.9, (0, 0, 10, 10))]}
        with patch("src.policy.rule_evaluators.time.monotonic", side_effect=[0, 1, 5, 10]):
            self.rules.evaluate(rule, dets, self.frame, camera_id="cam_active", policy_name="fire")
            fired = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_active", policy_name="fire")
            still_active = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_active", policy_name="fire")
            much_later = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_active", policy_name="fire")
        self.assertTrue(fired["triggered"])
        self.assertFalse(still_active["triggered"])
        self.assertFalse(much_later["triggered"])

    def test_reset_clears_camera_temporal_state(self):
        rule = {
            "type": "direct_detection",
            "classes": ["fire"],
            "temporal": {"min_hits": 2, "window_frames": 2, "max_gap_sec": 5},
        }
        dets = {"g": [Detection(0, "fire", 0.9, (0, 0, 10, 10))]}
        self.rules.evaluate(rule, dets, self.frame, camera_id="cam_reset", policy_name="fire")
        self.rules.reset("cam_reset")
        result = self.rules.evaluate(rule, dets, self.frame, camera_id="cam_reset", policy_name="fire")
        self.assertFalse(result["triggered"])
        self.assertEqual(result["temporal"]["hits"], 1)

    def test_temporal_gate_without_policy_name_is_no_op(self):
        """policy_name이 없으면 temporal gate는 적용되지 않는다(이전 호환성)."""
        rule = {
            "type": "direct_detection",
            "classes": ["fire"],
            "min_confidence": 0.4,
            "temporal": {"min_hits": 5, "max_gap_sec": 1, "clear_after_sec": 5},
        }
        dets = {"general_yolo": [Detection(0, "fire", 0.9, (10, 10, 40, 40))]}
        r = self.rules.evaluate(rule, dets, self.frame)  # no policy_name
        self.assertTrue(r["triggered"], "policy_name 부재 시 gate는 비활성")


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestEventPolicyEngine(unittest.TestCase):
    def setUp(self):
        self.frame = np.zeros((200, 200, 3), dtype=np.uint8)
        self.policies = {
            "catalog": {
                "fire": {"status": "active", "runtime": "detection"},
                "crowding": {"status": "active", "runtime": "detection"},
            },
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

    def test_all_enabled_life_safety_policies_are_provisional(self):
        engine = EventPolicyEngine.from_config("config/event_policies.yaml")
        life_safety = {"fire", "smoke", "fall", "fight"}
        missing = []
        for profile_name, profile in engine.policies.get("profiles", {}).items():
            for event_name, policy in profile.get("policies", {}).items():
                if (
                    event_name in life_safety
                    and policy.get("enabled", True)
                    and not policy.get("provisional_alert", False)
                ):
                    missing.append(f"{profile_name}.{event_name}")
        self.assertEqual(missing, [])

    def test_required_models_respects_enabled_events(self):
        engine = EventPolicyEngine(self.policies)
        self.assertEqual(
            engine.required_models("construction", ["fire"]),
            ["fire_smoke_yolo"],
        )
        self.assertEqual(engine.required_models("construction", []), [])

    def test_required_models_are_derived_only_from_selected_event_triggers(self):
        engine = EventPolicyEngine.from_config("config/event_policies.yaml")
        self.assertEqual(
            engine.required_models("construction", ["no_helmet", "fire"]),
            ["fire_smoke_yolo", "ppe_yolo"],
        )

    def test_unqualified_tracking_event_has_no_executable_contract(self):
        engine = EventPolicyEngine.from_config("config/event_policies.yaml")

        trigger = engine.trigger_for_event(
            "general", "line_crossing", ["line_crossing"], "tracking"
        )

        self.assertIsNone(trigger)
        self.assertIsNone(
            engine.trigger_for_event("general", "line_crossing", [], "tracking")
        )

    def test_unqualified_tracking_events_have_no_runtime_contract(self):
        engine = EventPolicyEngine.from_config("config/event_policies.yaml")

        line = engine.effective_tracking_policy(
            "general", "line_crossing", ["line_crossing"]
        )
        loitering = engine.effective_tracking_policy(
            "general", "loitering", ["loitering"]
        )

        self.assertIsNone(line)
        self.assertIsNone(loitering)
        self.assertEqual(engine.tracking_events_for_profile("general", ["line_crossing", "loitering"]), [])

    def test_loitering_is_not_evaluated_by_detection_runtime(self):
        engine = EventPolicyEngine.from_config("config/event_policies.yaml")

        decisions = engine.evaluate(
            "general",
            {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]},
            self.frame,
            "cam_loitering",
            enabled_events=["loitering"],
        )

        self.assertEqual(decisions, [])
        self.assertEqual(engine.required_models("general", ["loitering"]), [])

    def test_cross_domain_explicit_event_uses_canonical_policy(self):
        policies = {
            "catalog": {
                "no_helmet": {
                    "status": "active",
                    "runtime": "detection",
                    "canonical_profile": "construction",
                }
            },
            "profiles": {
                "general": {"policies": {}},
                "construction": {
                    "policies": {
                        "no_helmet": {
                            "enabled": True,
                            "trigger": {"models": ["general_yolo"], "classes": ["person"]},
                            "rule": {"type": "direct_detection"},
                            "vlm": "never",
                        }
                    }
                },
            },
        }
        engine = EventPolicyEngine(policies)
        self.assertEqual(engine.required_models("general", ["no_helmet"]), ["general_yolo"])
        decisions = engine.evaluate(
            "general",
            {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]},
            self.frame,
            "cam_cross",
            enabled_events=["no_helmet"],
        )
        self.assertEqual([item.event.event_type for item in decisions], ["no_helmet"])

    def test_mutable_catalog_cannot_relabel_experimental_event_as_active(self):
        policies = {
            "catalog": {"fall": {"status": "active", "runtime": "detection"}},
            "profiles": {"general": {"policies": {"fall": {
                "trigger": {"models": ["general_yolo"], "classes": ["person"]},
                "rule": {"type": "direct_detection"},
            }}}},
        }
        engine = EventPolicyEngine(policies)
        self.assertEqual(engine.required_models("general", ["fall"]), [])
        self.assertEqual(engine.behavior_plans("general", ["fall"]), [])
        self.assertEqual(engine.evaluate(
            "general",
            {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]},
            self.frame,
            "cam_fall",
            enabled_events=["fall"],
        ), [])

    def test_planned_health_event_is_not_evaluated_as_detection(self):
        engine = EventPolicyEngine.from_config("config/event_policies.yaml")
        self.assertEqual(engine.required_models("general", ["camera_disconnect"]), [])
        decisions = engine.evaluate(
            "general",
            {"general_yolo": [Detection(0, "person", 0.9, (0, 0, 10, 20))]},
            self.frame,
            "cam_health",
            enabled_events=["camera_disconnect"],
        )
        self.assertEqual(decisions, [])

    def test_project_fire_temporal_policy_is_reachable_at_sampled_cadence(self):
        engine = EventPolicyEngine.from_config("config/event_policies.yaml")
        detections = {
            "fire_smoke_yolo": [Detection(0, "fire", 0.9, (0, 0, 20, 20))]
        }
        decisions = []
        with patch(
            "src.policy.rule_evaluators.time.monotonic",
            side_effect=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        ):
            for _ in range(6):
                decisions = engine.evaluate(
                    "general",
                    detections,
                    self.frame,
                    "cam_fire_cadence",
                    enabled_events=["fire"],
                )
        self.assertEqual([item.event.event_type for item in decisions], ["fire"])

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
