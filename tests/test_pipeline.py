"""
안전 관리 파이프라인 테스트
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

import os
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.utils.config import ConfigLoader


class TestPureConfig(unittest.TestCase):
    """런타임 영상/AI 의존성 없이 실행 가능한 순수 설정 테스트"""

    def test_load_env_does_not_override_existing_value(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as env_file:
            env_file.write("KSI_TEST_VALUE=from_file\n")
            env_path = env_file.name

        try:
            os.environ["KSI_TEST_VALUE"] = "existing"
            ConfigLoader.load_env(env_path)
            self.assertEqual(os.environ["KSI_TEST_VALUE"], "existing")
        finally:
            os.environ.pop("KSI_TEST_VALUE", None)
            os.unlink(env_path)

    def test_load_env_sets_missing_value(self):
        with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as env_file:
            env_file.write("KSI_TEST_VALUE=from_file\n")
            env_path = env_file.name

        try:
            os.environ.pop("KSI_TEST_VALUE", None)
            ConfigLoader.load_env(env_path)
            self.assertEqual(os.environ["KSI_TEST_VALUE"], "from_file")
        finally:
            os.environ.pop("KSI_TEST_VALUE", None)
            os.unlink(env_path)

try:
    import numpy as np

    from src.alerts.alert_manager import AlertConfig, AlertManager
    from src.capture.rtsp_capture import CameraManager, RTSPCapture
    from src.detection.yolo_detector import Detection, YOLODetector
    from src.pipeline.pipeline import SafetyEvent, SafetyPipeline

    HAS_RUNTIME_DEPS = True
except ModuleNotFoundError:
    HAS_RUNTIME_DEPS = False

    class YOLODetector:  # type: ignore[no-redef]
        def _init_session(self):
            pass


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestCapture(unittest.TestCase):
    """캡처 모듈 테스트"""

    def test_camera_manager(self):
        manager = CameraManager()
        cam = manager.add_camera("test", "rtsp://test")
        self.assertIn("test", manager.cameras)
        self.assertEqual(cam.camera_id, "test")

    def test_rtsp_initialization(self):
        capture = RTSPCapture("test_cam", "rtsp://test")
        self.assertEqual(capture.camera_id, "test_cam")
        self.assertEqual(capture.stream_url, "rtsp://test")
        self.assertFalse(capture.is_running)


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestYOLODetector(unittest.TestCase):
    """탐지 모듈 테스트"""

    @patch.object(YOLODetector, '_init_session', return_value=None)
    def test_detection_initialization(self, _mock_init):
        detector = YOLODetector(
            model_path="./models/yolo26.onnx",
            input_size=(640, 640),
            conf_threshold=0.25
        )
        self.assertEqual(detector.input_size, (640, 640))
        self.assertEqual(detector.conf_threshold, 0.25)

    @patch.object(YOLODetector, '_init_session', return_value=None)
    def test_default_class_profile_uses_coco(self, _mock_init):
        detector = YOLODetector(model_path="./models/yolo26.onnx")

        self.assertEqual(detector.classes[0], "person")
        self.assertEqual(detector.classes[1], "bicycle")
        self.assertEqual(detector.classes[79], "toothbrush")

    @patch.object(YOLODetector, '_init_session', return_value=None)
    def test_safety_custom_class_profile(self, _mock_init):
        detector = YOLODetector(
            model_path="./models/yolo26.onnx",
            class_profile="safety_custom",
        )

        self.assertEqual(detector.classes[1], "helmet")
        self.assertEqual(detector.classes[3], "fire")

    @patch.object(YOLODetector, '_init_session', return_value=None)
    def test_classes_string_is_split_safely(self, _mock_init):
        detector = YOLODetector(
            model_path="./models/yolo26.onnx",
            classes="person,helmet,vest",
        )

        self.assertEqual(detector.classes, {0: "person", 1: "helmet", 2: "vest"})

    def test_detection_object(self):
        det = Detection(
            class_id=0,
            class_name="person",
            confidence=0.95,
            bbox=(100, 100, 200, 300)
        )
        self.assertEqual(det.class_name, "person")
        self.assertGreater(det.confidence, 0.9)

    def test_yolo26_final_output_postprocess(self):
        detector = object.__new__(YOLODetector)
        detector.input_size = (640, 640)
        detector.conf_threshold = 0.25
        detector.iou_threshold = 0.45
        detector.classes = YOLODetector.DEFAULT_CLASSES
        detector.postprocess_mode = "nms_free"

        outputs = [np.array([[[10, 20, 110, 220, 0.9, 0]]], dtype=np.float32)]
        detections = detector._postprocess(outputs, original_shape=(640, 640))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].class_name, "person")
        self.assertEqual(detections[0].bbox, (10, 20, 110, 220))

    def test_raw_yolo_output_postprocess(self):
        detector = object.__new__(YOLODetector)
        detector.input_size = (640, 640)
        detector.conf_threshold = 0.25
        detector.iou_threshold = 0.45
        detector.classes = YOLODetector.DEFAULT_CLASSES
        detector.postprocess_mode = "nms"

        outputs = [np.array([[[320, 320, 100, 100, 0.95, 0.05]]], dtype=np.float32)]
        detections = detector._postprocess(outputs, original_shape=(640, 640))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].class_name, "person")

    def test_raw_yolo_normalized_coords_postprocess(self):
        detector = object.__new__(YOLODetector)
        detector.input_size = (640, 640)
        detector.conf_threshold = 0.25
        detector.iou_threshold = 0.45
        detector.classes = YOLODetector.DEFAULT_CLASSES
        detector.postprocess_mode = "nms"

        outputs = [np.array([[[0.5, 0.5, 0.25, 0.5, 0.95, 0.05]]], dtype=np.float32)]
        detections = detector._postprocess(outputs, original_shape=(640, 640))

        self.assertEqual(len(detections), 1)
        self.assertEqual(detections[0].bbox, (240, 160, 400, 480))

    def test_safety_gear_skips_when_coco_model_lacks_gear_classes(self):
        detector = object.__new__(YOLODetector)
        detector.classes = YOLODetector.COCO_CLASSES

        result = detector.detect_safety_gear([
            Detection(0, "person", 0.9, (0, 0, 100, 200))
        ])

        self.assertEqual(len(result["persons"]), 1)
        self.assertEqual(result["gear"], [])
        self.assertEqual(result["violations"], [])

    def test_safety_gear_violation_when_custom_model_supports_gear(self):
        detector = object.__new__(YOLODetector)
        detector.classes = YOLODetector.DEFAULT_CLASSES

        result = detector.detect_safety_gear([
            Detection(0, "person", 0.9, (0, 0, 100, 200))
        ])

        self.assertEqual(len(result["violations"]), 1)


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestSafetyPipeline(unittest.TestCase):
    """파이프라인 테스트"""

    @patch('src.detection.yolo_detector.YOLODetector')
    def test_event_creation(self, mock_detector):
        SafetyPipeline(detector=mock_detector, skip_frames=5, alert_cooldown=30.0)
        event = SafetyEvent(
            event_id="test_001",
            camera_id="cam_001",
            event_type="person_detected",
            timestamp=__import__('datetime').datetime.now(),
            confidence=0.95
        )

        self.assertEqual(event.camera_id, "cam_001")
        self.assertEqual(event.event_type, "person_detected")

    def test_status(self):
        mock_detector = Mock()
        pipeline = SafetyPipeline(detector=mock_detector)
        status = pipeline.get_status()
        self.assertIn("running", status)
        self.assertFalse(status["running"])

    def test_safety_gear_violation_event_emit(self):
        mock_detector = Mock()
        pipeline = SafetyPipeline(detector=mock_detector, alert_cooldown=0)

        events = []
        pipeline.add_alert_callback(events.append)
        pipeline._emit_event(
            camera_id="cam_001",
            event_type="safety_gear_violation",
            timestamp=__import__('datetime').datetime.now(),
            detections=[Detection(0, "person", 0.9, (0, 0, 100, 200))],
            image=np.zeros((10, 10, 3), dtype=np.uint8)
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "safety_gear_violation")

    def test_vlm_observation_emits_analysis_without_alert_callback(self):
        mock_detector = Mock()
        pipeline = SafetyPipeline(
            detector=mock_detector,
            alert_cooldown=0,
            vlm_cooldown=0,
            vlm_trigger_classes=["person"],
        )
        pipeline._vlm_executor = Mock()

        alerts = []
        analyses = []
        pipeline.add_alert_callback(alerts.append)
        pipeline.add_analysis_callback(analyses.append)

        pipeline._emit_vlm_observation_if_needed(
            camera_id="cam_001",
            timestamp=__import__('datetime').datetime.now(),
            detections=[Detection(0, "person", 0.9, (0, 0, 100, 200))],
            image=np.zeros((10, 10, 3), dtype=np.uint8),
        )

        self.assertEqual(alerts, [])
        self.assertEqual(len(analyses), 1)
        self.assertEqual(analyses[0].event_type, "vlm_analysis_candidate")
        pipeline._vlm_executor.submit.assert_called_once()

    def test_vlm_observation_respects_cooldown(self):
        mock_detector = Mock()
        pipeline = SafetyPipeline(
            detector=mock_detector,
            vlm_cooldown=60,
            vlm_trigger_classes=["person"],
        )
        pipeline._vlm_executor = Mock()

        analyses = []
        pipeline.add_analysis_callback(analyses.append)
        kwargs = {
            "camera_id": "cam_001",
            "timestamp": __import__('datetime').datetime.now(),
            "detections": [Detection(0, "person", 0.9, (0, 0, 100, 200))],
            "image": np.zeros((10, 10, 3), dtype=np.uint8),
        }

        pipeline._emit_vlm_observation_if_needed(**kwargs)
        pipeline._emit_vlm_observation_if_needed(**kwargs)

        self.assertEqual(len(analyses), 1)
        pipeline._vlm_executor.submit.assert_called_once()

    def test_vlm_cache_skips_duplicate_non_critical_events(self):
        pipeline = SafetyPipeline(detector=Mock(), vlm_cache_ttl_sec=60)
        pipeline._vlm_executor = Mock()
        event = SafetyEvent(
            event_id="evt_1",
            camera_id="cam_001",
            event_type="no_helmet",
            timestamp=__import__('datetime').datetime.now(),
            confidence=0.8,
            image=np.zeros((10, 10, 3), dtype=np.uint8),
            alert_level="medium",
        )

        self.assertTrue(pipeline._submit_vlm_analysis(event))
        self.assertFalse(pipeline._submit_vlm_analysis(event))

        pipeline._vlm_executor.submit.assert_called_once()
        stats = pipeline.get_vlm_cache_stats()
        self.assertEqual(stats["misses"], 1)
        self.assertEqual(stats["hits"], 1)
        self.assertEqual(stats["bypasses"], 0)

    def test_vlm_cache_bypasses_high_priority_events(self):
        pipeline = SafetyPipeline(detector=Mock(), vlm_cache_ttl_sec=60)
        pipeline._vlm_executor = Mock()
        event = SafetyEvent(
            event_id="evt_2",
            camera_id="cam_001",
            event_type="fire",
            timestamp=__import__('datetime').datetime.now(),
            confidence=0.95,
            image=np.zeros((10, 10, 3), dtype=np.uint8),
            alert_level="critical",
        )

        self.assertTrue(pipeline._submit_vlm_analysis(event))
        self.assertTrue(pipeline._submit_vlm_analysis(event))

        self.assertEqual(pipeline._vlm_executor.submit.call_count, 2)
        stats = pipeline.get_vlm_cache_stats()
        self.assertEqual(stats["bypasses"], 2)


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestAlertManager(unittest.TestCase):
    """알람 관리자 테스트"""

    def test_alert_config(self):
        config = AlertConfig(enabled=True, log_file="./logs/test.log", save_images=False)
        self.assertTrue(config.enabled)
        self.assertEqual(config.log_file, "./logs/test.log")

    def test_alert_manager(self):
        config = AlertConfig(enabled=True, save_images=False, log_file="./logs/test_alerts.log")
        manager = AlertManager(config)
        self.assertTrue(manager.config.enabled)

        event = SafetyEvent(
            event_id="test_evt",
            camera_id="cam_001",
            event_type="person_detected",
            timestamp=__import__('datetime').datetime.now(),
            confidence=0.95
        )
        manager.handle_event(event)
        self.assertEqual(manager.event_count, 1)

    def test_alert_log_parser_reads_actual_formatter_line(self):
        from src.alerts.alert_manager import parse_alert_log_line

        line = '2026-06-10 12:00:00 | INFO | EVENT | {"event_type": "fire", "camera_id": "cam_001"}'

        parsed = parse_alert_log_line(line)

        self.assertEqual(parsed["event_type"], "fire")
        self.assertEqual(parsed["camera_id"], "cam_001")

    def test_get_event_log_reads_written_event(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = str(Path(tmp) / "alerts.log")
            manager = AlertManager(AlertConfig(enabled=True, save_images=False, console_output=False, log_file=log_file))
            event = SafetyEvent(
                event_id="test_evt_log",
                camera_id="cam_001",
                event_type="person_detected",
                timestamp=__import__('datetime').datetime.now(),
                confidence=0.95,
            )

            manager.handle_event(event)
            for handler in manager.alert_logger.handlers:
                handler.flush()

            events = manager.get_event_log(limit=10)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "person_detected")


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestPolicyPipelineIntegration(unittest.TestCase):
    """Phase 4: MultiModelRegistry + EventPolicyEngine 통합 테스트"""

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

    def test_safety_event_has_policy_fields(self):
        """SafetyEvent가 정책 필드를 기본값으로 포함하는지 확인"""
        event = SafetyEvent(
            event_id="test_001",
            camera_id="cam_001",
            event_type="fire_detected",
            timestamp=__import__('datetime').datetime.now(),
            confidence=0.95,
        )

        self.assertEqual(event.alert_level, "low")
        self.assertEqual(event.cooldown_sec, 30.0)
        self.assertFalse(event.vlm_needed)
        self.assertEqual(list(event.source_models), [])
        self.assertEqual(event.rule_name, "")

    def test_safety_event_to_dict_includes_policy_fields(self):
        """SafetyEvent.to_dict()가 정책 필드를 포함하는지 확인"""
        event = SafetyEvent(
            event_id="test_002",
            camera_id="cam_001",
            event_type="fire_detected",
            timestamp=__import__('datetime').datetime.now(),
            confidence=0.95,
            alert_level="critical",
            cooldown_sec=15.0,
            vlm_needed=True,
            source_models=["fire_smoke_yolo"],
            rule_name="direct_detection",
        )

        d = event.to_dict()
        self.assertEqual(d["alert_level"], "critical")
        self.assertEqual(d["cooldown_sec"], 15.0)
        self.assertTrue(d["vlm_needed"])
        self.assertEqual(d["source_models"], ["fire_smoke_yolo"])
        self.assertEqual(d["rule_name"], "direct_detection")

    def test_pipeline_policy_mode_flag(self):
        """policy_engine + model_registry 주입 시 use_policy_mode=True"""
        mock_registry = Mock()
        mock_engine = Mock()
        pipeline = SafetyPipeline(
            detector=None,
            model_registry=mock_registry,
            policy_engine=mock_engine,
        )

        self.assertTrue(pipeline.use_policy_mode)
        self.assertEqual(pipeline.model_registry, mock_registry)
        self.assertEqual(pipeline.policy_engine, mock_engine)

    def test_pipeline_legacy_mode_flag(self):
        """detector만 주입 시 use_policy_mode=False"""
        mock_detector = Mock()
        pipeline = SafetyPipeline(detector=mock_detector)

        self.assertFalse(pipeline.use_policy_mode)
        self.assertIsNone(pipeline.model_registry)
        self.assertIsNone(pipeline.policy_engine)

    def test_pipeline_set_camera_profile(self):
        """set_camera_profile로 카메라별 프로필 설정"""
        mock_detector = Mock()
        pipeline = SafetyPipeline(detector=mock_detector)

        pipeline.set_camera_profile("cam_001", {"domain": "construction", "events": ["fire"]})
        self.assertEqual(pipeline._camera_profiles["cam_001"]["domain"], "construction")

    def test_policy_engine_creates_event_with_correct_fields(self):
        """EventPolicyEngine가 SafetyEvent에 정책 필드를 직접 설정하는지 확인"""
        from src.policy.event_policy_engine import EventPolicyEngine

        engine = EventPolicyEngine(self.policies)
        decisions = engine.evaluate(
            profile_name="construction",
            model_detections={"fire_smoke_yolo": [Detection(0, "fire", 0.9, (0, 0, 20, 20))]},
            frame=self.frame,
            camera_id="cam_001",
        )

        self.assertEqual(len(decisions), 1)
        decision = decisions[0]
        event = decision.event

        # SafetyEvent 필드가 직접 설정되어 있어야 함 (동적 속성 아님)
        self.assertEqual(event.alert_level, "critical")
        self.assertTrue(event.vlm_needed)
        self.assertEqual(list(event.source_models), ["fire_smoke_yolo"])
        self.assertEqual(event.rule_name, "direct_detection")

    def test_policy_engine_fire_and_crowding_both_triggered(self):
        """여러 정책이 동시에 평가되어 여러 결정이 생성되는지 확인"""
        from src.policy.event_policy_engine import EventPolicyEngine

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
        )

        event_types = {d.event.event_type for d in decisions}
        self.assertIn("fire", event_types)
        self.assertIn("crowding", event_types)
        self.assertEqual(len(decisions), 2)

    def test_legacy_pipeline_emit_event_still_works(self):
        """기존 단일 detector 모드에서 _emit_event가 정상 동작하는지 확인"""
        mock_detector = Mock()
        pipeline = SafetyPipeline(detector=mock_detector, alert_cooldown=0)

        events = []
        pipeline.add_alert_callback(events.append)
        pipeline._emit_event(
            camera_id="cam_001",
            event_type="safety_gear_violation",
            timestamp=__import__('datetime').datetime.now(),
            detections=[Detection(0, "person", 0.9, (0, 0, 100, 200))],
            image=np.zeros((10, 10, 3), dtype=np.uint8)
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "safety_gear_violation")


@unittest.skipUnless(HAS_RUNTIME_DEPS, "requirements.txt 의존성 설치 후 실행 가능")
class TestPipelineVlmWiringBatch2(unittest.TestCase):
    """Batch2 회귀 가드: VLM 결과 배선·파싱실패 표식·backpressure·enabled_events·get_status."""

    def _event(self, **kw):
        base = {
            "event_id": "e1",
            "camera_id": "cam_001",
            "event_type": "fire",
            "timestamp": __import__("datetime").datetime.now(),
            "confidence": 0.8,
            "image": np.zeros((10, 10, 3), dtype=np.uint8),
        }
        base.update(kw)
        return SafetyEvent(**base)

    def test_safety_event_vlm_fields_default_and_to_dict(self):
        ev = self._event()
        self.assertEqual(ev.vlm_detected_events, [])
        self.assertEqual(ev.vlm_recommended_action, "")
        self.assertIsNone(ev.vlm_parse_success)
        self.assertEqual(ev.vlm_raw_response, "")
        d = ev.to_dict()
        for key in (
            "vlm_detected_events",
            "vlm_recommended_action",
            "vlm_parse_success",
            "vlm_raw_response",
        ):
            self.assertIn(key, d)

    def test_analyze_with_vlm_copies_extended_fields_and_marks_parse_failure(self):
        pipeline = SafetyPipeline(detector=Mock())
        pipeline.prompt_selector = Mock()
        pipeline.prompt_selector.build_prompt.return_value = "prompt"
        result = Mock()
        result.description = "원본 텍스트"
        result.risk_level = "low"
        result.confidence = 0.0
        result.inference_time_ms = 5.0
        result.detected_events = ["fall"]
        result.recommended_action = "관리자 확인 요망"
        result.raw_response = "not-json"
        result.parse_success = False
        provider = Mock()
        provider.analyze.return_value = result
        pipeline.vlm_provider = provider

        ev = self._event()
        pipeline._analyze_with_vlm(ev, None)

        self.assertEqual(ev.vlm_detected_events, ["fall"])
        self.assertEqual(ev.vlm_recommended_action, "관리자 확인 요망")
        self.assertFalse(ev.vlm_parse_success, "파싱 실패가 event에 표식돼야 한다")
        self.assertEqual(ev.vlm_raw_response, "not-json")

    def test_legacy_emit_event_submits_vlm(self):
        """회귀 가드: legacy _emit_event(fire/smoke/fall)도 VLM 분석에 제출돼야 한다(baseline 동작)."""
        pipeline = SafetyPipeline(detector=Mock(), alert_cooldown=0)
        pipeline._vlm_executor = Mock()
        alerts = []
        pipeline.add_alert_callback(alerts.append)
        pipeline._emit_event(
            camera_id="cam_001",
            event_type="fire_detected",
            timestamp=__import__("datetime").datetime.now(),
            detections=[Detection(0, "fire", 0.9, (0, 0, 50, 50))],
            image=np.zeros((10, 10, 3), dtype=np.uint8),
        )
        self.assertEqual(len(alerts), 1, "즉시 알림 콜백은 호출돼야 한다")
        pipeline._vlm_executor.submit.assert_called_once()

    def test_backpressure_drops_low_priority_but_bypasses_critical(self):
        pipeline = SafetyPipeline(detector=Mock())
        pipeline._vlm_executor = Mock()
        pipeline._vlm_queue_max_in_flight = 1
        pipeline._vlm_in_flight = 1  # 포화 상태

        low = self._event(event_type="person_detected", alert_level="low")
        self.assertFalse(pipeline._submit_vlm_analysis(low), "포화 시 low는 drop돼야 한다")
        pipeline._vlm_executor.submit.assert_not_called()
        self.assertGreaterEqual(pipeline._vlm_dropped_count, 1)

        # critical은 backpressure를 우회해 제출돼야 한다
        crit = self._event(event_type="fire", alert_level="critical")
        self.assertTrue(pipeline._submit_vlm_analysis(crit))
        pipeline._vlm_executor.submit.assert_called_once()

    def test_dropped_event_does_not_poison_cache(self):
        """drop된 이벤트가 TTL cache를 오염시켜 다음 정상 이벤트를 억제하면 안 된다."""
        pipeline = SafetyPipeline(detector=Mock(), vlm_cache_ttl_sec=60)
        pipeline._vlm_executor = Mock()
        pipeline._vlm_queue_max_in_flight = 1
        pipeline._vlm_in_flight = 1  # 포화 → 다음 low는 drop

        dropped = self._event(event_type="no_helmet", alert_level="medium")
        self.assertFalse(pipeline._submit_vlm_analysis(dropped))

        # 포화 해소 후 같은 event_type 정상 이벤트는 cache-hit 없이 제출돼야 한다
        pipeline._vlm_in_flight = 0
        ok = self._event(event_type="no_helmet", alert_level="medium")
        self.assertTrue(
            pipeline._submit_vlm_analysis(ok),
            "drop이 cache를 오염시켜 다음 정상 이벤트가 억제되면 안 된다",
        )
        pipeline._vlm_executor.submit.assert_called_once()

    def test_enabled_events_empty_list_is_preserved_not_none(self):
        """enabled_events=[](전부 비활성)가 None으로 바뀌어 필터 해제되면 안 된다."""
        captured = {}

        def fake_eval(**kw):
            captured["enabled_events"] = kw.get("enabled_events")
            return []

        engine = Mock()
        engine.evaluate.side_effect = fake_eval
        registry = Mock()
        registry.detect_all.return_value = {}

        pipeline = SafetyPipeline(model_registry=registry, policy_engine=engine)
        ts = __import__("datetime").datetime.now()
        img = np.zeros((10, 10, 3), dtype=np.uint8)

        pipeline.set_camera_profile("cam_001", {"domain": "construction", "enabled_events": []})
        pipeline._process_frame_with_policy("cam_001", img, ts)
        self.assertEqual(captured["enabled_events"], [], "빈 리스트가 그대로 전달돼야 한다")

        # events fallback (enabled_events 없을 때)
        pipeline.set_camera_profile("cam_002", {"domain": "construction", "events": ["fire"]})
        pipeline._process_frame_with_policy("cam_002", img, ts)
        self.assertEqual(captured["enabled_events"], ["fire"])

        # 둘 다 없으면 None(필터 없음)
        pipeline.set_camera_profile("cam_003", {"domain": "construction"})
        pipeline._process_frame_with_policy("cam_003", img, ts)
        self.assertIsNone(captured["enabled_events"])

    def test_get_status_includes_operational_metrics(self):
        registry = Mock()
        registry.get_model_metrics.return_value = {"general_yolo": {"inference_count": 3}}
        pipeline = SafetyPipeline(model_registry=registry, policy_engine=Mock())
        status = pipeline.get_status()
        for key in ("running", "cameras", "vlm_queue", "vlm_cache", "vlm_provider", "models"):
            self.assertIn(key, status)
        self.assertEqual(status["models"], {"general_yolo": {"inference_count": 3}})
        self.assertIn("in_flight", status["vlm_queue"])

    def test_inflight_event_image_protected_from_eviction(self):
        """회귀 가드(#1/#4): VLM 큐 대기 중(in-flight) 이벤트의 이미지는 eviction에서 보호된다."""
        pipeline = SafetyPipeline(detector=Mock())
        pipeline._history_image_keep = 2
        img = np.zeros((4, 4, 3), dtype=np.uint8)

        inflight = self._event(event_id="inflight", image=img.copy())
        pipeline._inflight_event_ids.add("inflight")
        pipeline._record_event(inflight)
        # keep(2)를 넉넉히 초과하도록 이후 이벤트를 다수 기록
        for i in range(6):
            pipeline._record_event(self._event(event_id=f"e{i}", image=img.copy()))

        self.assertIsNotNone(
            inflight.image, "in-flight 이벤트 이미지가 eviction으로 None이 되면 VLM·캡처가 누락된다"
        )

    def test_analyze_with_vlm_fires_callbacks_even_when_image_none(self):
        """회귀 가드(#6): image가 eviction으로 None이어도 지연 alert 콜백은 호출된다."""
        pipeline = SafetyPipeline(detector=Mock())
        pipeline.vlm_provider = Mock()
        ev = self._event(image=None)
        fired = []
        pipeline._analyze_with_vlm(ev, [fired.append])
        self.assertEqual(len(fired), 1, "image None이어도 지연 alert 콜백은 발송돼야 한다")

    def test_validate_target_classes_registry_mode_uses_get_model(self):
        """회귀 가드(#5): registry 모드 validate가 존재하지 않는 _detectors가 아니라 get_model을 쓴다."""
        reg = Mock()
        status = Mock()
        status.loaded = True
        reg.load_status = {"general_yolo": status}
        det = Mock()
        det.classes = {0: "person"}
        reg.get_model.return_value = det

        pipeline = SafetyPipeline(model_registry=reg, policy_engine=Mock())
        pipeline.target_classes = ["person", "fire"]
        unreachable = pipeline.validate_target_classes()

        reg.get_model.assert_called_with("general_yolo")
        self.assertIn("fire", unreachable, "COCO 미산출 클래스(fire)는 unreachable로 경고돼야 한다")
        self.assertNotIn("person", unreachable)
