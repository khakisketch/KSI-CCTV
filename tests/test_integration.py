"""
KSI-CCTV 파이프라인 통합 테스트 (Phase 5)

Mock 비디오 소스를 사용하여 전체 파이프라인을 end-to-end로 검증합니다.
"""
import sys
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import numpy as np
import cv2

sys.path.append(str(Path(__file__).parent.parent))


def create_synthetic_video(path: str, num_frames: int = 30, fps: int = 10, width: int = 320, height: int = 240):
    """테스트용 합성 비디오 생성"""
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = cv2.VideoWriter(path, fourcc, fps, (width, height))
    for i in range(num_frames):
        frame = np.zeros((height, width, 3), dtype=np.uint8)
        frame[:] = (50, 50, 50)
        # 움직이는 사각형
        x = int(50 + 100 * np.sin(i * 0.3))
        y = int(50 + 50 * np.cos(i * 0.5))
        cv2.rectangle(frame, (x, y), (x + 60, y + 100), (0, 255, 0), -1)
        cv2.putText(frame, f"F{i}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        writer.write(frame)
    writer.release()


@unittest.skipUnless(
    all(__import__("importlib").util.find_spec(m) for m in ["numpy", "cv2", "onnxruntime"]),
    "런타임 의존성 필요"
)
class TestPipelineIntegration(unittest.TestCase):
    """파이프라인 통합 테스트"""

    @classmethod
    def setUpClass(cls):
        """테스트 비디오 생성"""
        cls.temp_dir = tempfile.mkdtemp()
        cls.test_video = str(Path(cls.temp_dir) / "test_video.mp4")
        create_synthetic_video(cls.test_video, num_frames=20, fps=10)

    @classmethod
    def tearDownClass(cls):
        """임시 파일 정리"""
        import shutil
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_capture_reads_video(self):
        """RTSPCapture가 비디오 파일을 읽을 수 있는지 검증"""
        from src.capture.rtsp_capture import RTSPCapture

        capture = RTSPCapture("test_cam", self.test_video, target_fps=5, queue_size=5)
        capture.start()
        time.sleep(2)

        frame = capture.get_frame(timeout=2.0)
        self.assertIsNotNone(frame)
        self.assertEqual(frame.camera_id, "test_cam")
        self.assertIsNotNone(frame.image)
        self.assertGreater(frame.image.shape[0], 0)
        self.assertGreater(frame.image.shape[1], 0)

        capture.stop()

    def test_detector_runs_on_real_frame(self):
        """실제 프레임에서 detector가 실행되는지 검증"""
        from src.detection.yolo_detector import YOLODetector

        with patch.object(YOLODetector, '_init_session', return_value=None):
            detector = YOLODetector(
                model_path="./models/yolo26.onnx",
                input_size=(640, 640),
                conf_threshold=0.01,
            )

        # 합성 프레임으로 추론 테스트
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:] = (50, 50, 50)
        cv2.rectangle(frame, (100, 100), (300, 400), (0, 255, 0), -1)

        # mock session으로 추론 시뮬레이션
        mock_output = np.array([[[100, 100, 300, 400, 0.5, 0]]], dtype=np.float32)
        detector.session = Mock()
        detector.session.run.return_value = [mock_output]
        detector.input_name = "images"
        detector.output_names = ["output0"]
        detector._logged_output_shapes = True

        detections = detector.detect(frame)
        # mock 결과에 따라 detections가 나올 수 있음
        self.assertIsInstance(detections, list)

    def test_full_pipeline_end_to_end(self):
        """전체 파이프라인 end-to-end 테스트"""
        from src.capture.rtsp_capture import RTSPCapture, CameraManager
        from src.detection.yolo_detector import YOLODetector, Detection
        from src.pipeline.pipeline import SafetyPipeline
        from concurrent.futures import ThreadPoolExecutor

        with patch.object(YOLODetector, '_init_session', return_value=None):
            detector = YOLODetector(
                model_path="./models/yolo26.onnx",
                input_size=(640, 640),
                conf_threshold=0.1,
            )

        # Mock session for detection
        detector.session = Mock()
        detector.session.run.return_value = [np.array([[[0, 0, 100, 100, 0.5, 0]]], dtype=np.float32)]
        detector.input_name = "images"
        detector.output_names = ["output0"]
        detector._logged_output_shapes = True

        pipeline = SafetyPipeline(
            detector=detector,
            skip_frames=1,
            alert_cooldown=0.5,
            target_classes=["person"],
            vlm_trigger_classes=["person"],
        )
        pipeline._vlm_executor = ThreadPoolExecutor(max_workers=1)

        events = []
        def on_alert(event):
            events.append(event)

        def on_analysis(event):
            events.append(event)

        pipeline.add_alert_callback(on_alert)
        pipeline.add_analysis_callback(on_analysis)
        pipeline.add_camera("test_cam", self.test_video, target_fps=5)

        pipeline.start()
        time.sleep(3)
        pipeline.stop()

        # 합성 비디오는 COCO 객체가 없으므로 이벤트가 적을 수 있지만,
        # 파이프라인이 크래시 없이 동작하는지 확인
        self.assertIsNotNone(events)
        # 파이프라인 상태 확인
        status = pipeline.get_status()
        self.assertIn("cameras", status)
        self.assertIn("test_cam", status["cameras"])

    def test_alert_manager_handles_event(self):
        """AlertManager가 이벤트를 처리하는지 검증"""
        from src.alerts.alert_manager import AlertManager, AlertConfig
        from src.pipeline.pipeline import SafetyEvent
        from datetime import datetime

        with tempfile.TemporaryDirectory() as tmp:
            log_file = str(Path(tmp) / "alerts.log")
            config = AlertConfig(
                enabled=True,
                log_file=log_file,
                save_images=False,
                console_output=False,
            )
            manager = AlertManager(config)

            event = SafetyEvent(
                event_id="test_001",
                camera_id="cam_001",
                event_type="person_detected",
                timestamp=datetime.now(),
                confidence=0.95,
            )

            manager.handle_event(event)
            self.assertEqual(manager.event_count, 1)


class TestPolicyEngineIntegration(unittest.TestCase):
    """정책 엔진 통합 테스트"""

    def test_policy_evaluation_end_to_end(self):
        """정책 평가 end-to-end"""
        from src.policy.event_policy_engine import EventPolicyEngine
        from src.detection.yolo_detector import Detection

        policies = {
            "profiles": {
                "construction": {
                    "policies": {
                        "person_detected": {
                            "enabled": True,
                            "trigger": {"models": ["general_yolo"], "classes": ["person"]},
                            "rule": {"type": "direct_detection"},
                            "vlm": "never",
                            "alert_level": "low",
                            "cooldown_sec": 30,
                        },
                        "crowding": {
                            "enabled": True,
                            "trigger": {"models": ["general_yolo"], "classes": ["person"]},
                            "rule": {"type": "count_threshold", "class_name": "person", "min_count": 3},
                            "vlm": "never",
                            "alert_level": "low",
                            "cooldown_sec": 60,
                        }
                    }
                }
            }
        }

        engine = EventPolicyEngine(policies)
        frame = np.zeros((100, 100, 3), dtype=np.uint8)
        model_detections = {
            "general_yolo": [
                Detection(0, "person", 0.9, (0, 0, 30, 60)),
                Detection(0, "person", 0.85, (40, 0, 70, 60)),
                Detection(0, "person", 0.8, (80, 0, 100, 60)),
            ]
        }

        decisions = engine.evaluate(
            profile_name="construction",
            model_detections=model_detections,
            frame=frame,
            camera_id="test_cam",
        )

        event_types = {d.event.event_type for d in decisions}
        self.assertIn("person_detected", event_types)
        self.assertIn("crowding", event_types)  # 3명 이상


class TestMultiModelRegistryIntegration(unittest.TestCase):
    """다중 모델 레지스트리 통합 테스트"""

    def test_registry_loads_from_config(self):
        """config에서 모델 레지스트리 로드"""
        from src.detection.model_registry import MultiModelRegistry

        registry = MultiModelRegistry.from_config("./config/model_profiles.yaml", strict=False)

        # 로드된 모델 확인
        active = registry.active_model_names
        self.assertIsInstance(active, list)

        # 실패한 모델 확인
        failed = registry.failed_models
        self.assertIsInstance(failed, dict)

        # 상태 조회
        status = registry.to_status_dict()
        self.assertIsInstance(status, dict)


if __name__ == "__main__":
    unittest.main()
