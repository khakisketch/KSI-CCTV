"""Unit tests for feature-gated pose, ST-GCN, and bounded behavior state."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest

sys.path.append(str(Path(__file__).parent.parent))

from src.detection.pose_detector import PoseOnnxDetector
from src.detection.pose_models import ActionPrediction, PoseObservation
from src.detection.stgcn_detector import STGCNOnnxClassifier
from src.detection.yolo_detector import Detection
from src.pipeline.pipeline import SafetyPipeline
from src.policy.event_policy_engine import EventPolicyEngine
from src.tracking.behavior_buffer import BehaviorBuffer
from src.tracking.models import TrackPoint, TrackSnapshot


def _pose(timestamp: float = 1.0, keypoints: int = 2) -> PoseObservation:
    return PoseObservation([(11.0, 22.0, 0.9)] * keypoints, (10, 20, 30, 40), timestamp, 0.8)


def _pose_session(output: np.ndarray) -> Mock:
    session = Mock()
    session.run.return_value = [output]
    return session


def test_contracts_copy_mutable_inputs_and_reject_invalid_values():
    points = np.array([[1, 2, 0.5]], dtype=np.float32)
    observation = PoseObservation(points, [0, 0, 3, 4], 1, 0.5)
    points[0, 0] = 99
    assert observation.keypoints == ((1.0, 2.0, 0.5),)
    assert observation.bbox == (0.0, 0.0, 3.0, 4.0)
    with pytest.raises(ValueError, match="finite"):
        PoseObservation([(np.nan, 1, 1)], (0, 0, 1, 1), 1, 1)
    prediction = ActionPrediction("fall", 1, 0.75, [0.25, 0.75])
    assert prediction.class_name == "fall"
    with pytest.raises(ValueError, match="sum"):
        ActionPrediction("fall", 0, 0.8, [0.8, 0.8])


def test_pose_detector_disabled_and_missing_model_are_safe():
    disabled = PoseOnnxDetector(None, enabled=False, keypoint_count=1)
    assert disabled.detect(np.zeros((10, 10, 3), dtype=np.uint8), 1.0) == []
    missing = PoseOnnxDetector("/not/a/model.onnx", enabled=True, keypoint_count=1)
    assert not missing.available
    assert "not configured" in missing.status()["error"]


def test_pose_detector_selects_existing_provider_and_session_failure_is_safe(tmp_path):
    model = tmp_path / "pose.onnx"
    model.touch()
    with (
        patch(
            "src.detection.pose_detector.ort.get_available_providers",
            return_value=["CPUExecutionProvider"],
        ),
        patch(
            "src.detection.pose_detector.ort.InferenceSession",
            side_effect=RuntimeError("bad model"),
        ),
    ):
        detector = PoseOnnxDetector(model, enabled=True, keypoint_count=1)
    assert detector.session is None
    assert "initialization failed" in detector.error


@pytest.mark.parametrize("layout", ["bcn", "bnc"])
def test_pose_detector_restores_letterboxed_crop_coordinates(layout):
    detector = PoseOnnxDetector(
        None, enabled=False, input_size=(100, 100), keypoint_count=1, output_layout=layout
    )
    # Crop (x=10..60, y=20..40) letterboxes to 100x100 with top padding 30.
    row = np.array([40, 50, 40, 40, 0.9, 50, 50, 0.8], dtype=np.float32)
    output = row.reshape(1, -1, 1) if layout == "bcn" else row.reshape(1, 1, -1)
    detector.session = _pose_session(output)
    detector.enabled = True
    detector.input_name, detector.output_names = "images", ["output"]
    image = np.zeros((100, 100, 3), dtype=np.uint8)
    result = detector.detect(image, 5.0, (10, 20, 60, 40))
    assert len(result) == 1
    assert result[0].bbox == pytest.approx((20, 20, 40, 40))
    assert result[0].keypoints[0] == pytest.approx((35, 30, 0.8))


def test_pose_detector_rejects_malformed_and_nonfinite_output_without_raising():
    detector = PoseOnnxDetector(None, enabled=False, keypoint_count=1)
    detector.session = _pose_session(np.full((1, 8, 1), np.nan, dtype=np.float32))
    detector.enabled = True
    detector.input_name, detector.output_names = "images", ["output"]
    assert detector.detect(np.zeros((10, 10, 3), dtype=np.uint8), 1.0) == []
    assert detector.metrics.error_count == 1


def test_stgcn_normalizes_bbox_relative_nctvm_and_softmaxes_logits():
    classifier = STGCNOnnxClassifier(
        None, ["normal", "fall"], enabled=False, keypoint_count=2, sequence_length=2
    )
    prepared = classifier.prepare_input([_pose(1), _pose(2)])
    assert prepared.shape == (1, 3, 2, 2, 1)
    assert prepared[0, :, 0, 0, 0] == pytest.approx((0.05, 0.1, 0.9))
    classifier.session = _pose_session(np.array([[0.0, 2.0]], dtype=np.float32))
    classifier.enabled = True
    classifier.input_name = "data"
    prediction = classifier.classify([_pose(1), _pose(2)])
    assert prediction is not None
    assert prediction.action == "fall"
    assert sum(prediction.probabilities) == pytest.approx(1.0)


def test_stgcn_keeps_valid_probabilities_and_rejects_invalid_output():
    classifier = STGCNOnnxClassifier(
        None, ["normal", "fall"], enabled=False, keypoint_count=1, sequence_length=1
    )
    classifier.session = _pose_session(np.array([0.2, 0.8], dtype=np.float32))
    classifier.enabled = True
    classifier.input_name = "data"
    prediction = classifier.classify([_pose(keypoints=1)])
    assert prediction is not None and prediction.probabilities == pytest.approx((0.2, 0.8))
    classifier.session = _pose_session(np.array([np.nan, 1.0], dtype=np.float32))
    assert classifier.classify([_pose(keypoints=1)]) is None
    assert classifier.metrics.error_count == 1


def test_behavior_buffer_is_bounded_monotonic_and_camera_isolated():
    buffer = BehaviorBuffer(2, stale_after_sec=5, max_tracks_per_camera=2)
    assert buffer.append("a", 1, _pose(1))
    assert buffer.append("a", 1, _pose(2))
    assert len(buffer.sequence("a", 1) or ()) == 2
    assert not buffer.append("a", 1, _pose(2))
    assert buffer.status("a")["rejected_out_of_order"] == 1
    assert buffer.append("b", 1, _pose(1))
    assert buffer.sequence("b", 1) is None
    assert buffer.append("a", 2, _pose(3))
    assert buffer.append("a", 3, _pose(4))
    assert buffer.sequence("a", 1) is None
    assert buffer.status()["tracks"] == 3


def test_behavior_buffer_prunes_and_resets_per_camera_or_globally():
    buffer = BehaviorBuffer(1, stale_after_sec=5)
    buffer.append("a", 1, _pose(1))
    buffer.append("b", 1, _pose(2))
    assert buffer.prune_stale(7) == 1
    assert buffer.sequence("a", 1) is None
    assert buffer.sequence("b", 1) is not None
    buffer.reset("b")
    assert buffer.status()["tracks"] == 0
    buffer.append("a", 2, _pose(10))
    buffer.reset()
    assert buffer.status()["cameras"] == 0


def test_catalog_behavior_plan_is_merged_and_disabled_plan_keeps_required_models_stable():
    policies = {
        "catalog": {"fire": {"status": "active", "runtime": "detection", "behavior": {
            "enabled": False, "person": {"models": ["person_yolo"], "classes": ["person"]},
            "pose_model": "pose", "action_model": "action",
        }}},
        "profiles": {"general": {"policies": {"fire": {
            "trigger": {"models": ["person_yolo"], "classes": ["person"]},
            "rule": {"type": "pose_anomaly"},
        }}}},
    }
    engine = EventPolicyEngine(policies)
    assert engine.required_models("general", ["fire"]) == ["person_yolo"]
    assert engine.behavior_plans("general", ["fire"]) == []
    policies["catalog"]["fire"]["behavior"]["enabled"] = True
    assert engine.required_models("general", ["fire"]) == ["action", "person_yolo", "pose"]
    assert engine.behavior_plans("general", ["fire"])[0]["action_model"] == "action"


def test_behavior_pipeline_associates_once_emits_private_action_event_and_resets():
    from datetime import datetime
    from unittest.mock import Mock

    timestamp = datetime(2026, 1, 1)
    track = TrackSnapshot(
        "cam", 7, "person", (10, 10, 30, 50), 0.9, timestamp, timestamp,
        (TrackPoint(20, 50, timestamp),),
    )
    policies = {
        "catalog": {"fire": {"status": "active", "runtime": "detection", "behavior": {
            "enabled": True, "person": {"models": ["person_yolo"], "classes": ["person"]},
            "pose_model": "pose", "action_model": "action", "action_label": "fire",
            "min_confidence": 0.5, "temporal": {"min_hits": 1},
        }}},
        "profiles": {"general": {"policies": {"fire": {
            "trigger": {"models": ["person_yolo"], "classes": ["person"]},
            "rule": {"type": "pose_anomaly"}, "cooldown_sec": 0,
        }}}},
    }
    registry = Mock()
    registry.models = {"person_yolo": Mock()}
    registry.pose_detectors = {"pose": Mock()}
    registry.action_classifiers = {"action": Mock(sequence_length=1)}
    registry.detect_all.return_value = {"person_yolo": [Detection(0, "person", 0.9, track.bbox)]}
    registry.detect_poses.return_value = [
        PoseObservation([(11, 22, 0.9), (12, 23, 0.9)], track.bbox, timestamp.timestamp(), 0.9)
    ]
    registry.classify_action.return_value = ActionPrediction("fire", 0, 0.9, [0.9, 0.1])
    tracker = Mock()
    tracker.update.return_value = [track]
    pipeline = SafetyPipeline(
        model_registry=registry, policy_engine=EventPolicyEngine(policies), track_manager=tracker,
        behavior_settings={"enabled": True, "inference_stride": 1, "iou_threshold": 0.5},
    )
    pipeline.set_camera_profile("cam", {"domain": "general", "enabled_events": ["fire"]})
    emitted = []
    pipeline.add_alert_callback(emitted.append)
    pipeline._process_frame_with_policy("cam", np.zeros((80, 80, 3), dtype=np.uint8), timestamp)
    assert tracker.update.call_count == 1
    assert len(emitted) == 1
    assert emitted[0].event_type == "fire"
    assert emitted[0].metadata["action"] == "fire"
    assert set(emitted[0].metadata) == {"track_id", "action", "confidence", "model_names", "sequence_length", "first_seen", "last_seen"}
    assert pipeline.get_status()["behavior"]["counters"]["inferences"] == 1
    pipeline.remove_camera("cam")
    assert pipeline._behavior_buffers["action"].status()["tracks"] == 0


def test_iou_association_rejects_ambiguous_pose_matches():
    pipeline = SafetyPipeline(behavior_settings={"enabled": True, "iou_threshold": 0.1})
    from datetime import datetime

    now = datetime.now()
    tracks = [
        TrackSnapshot("cam", 1, "person", (0, 0, 20, 20), 1, now, now, ()),
        TrackSnapshot("cam", 2, "person", (0, 0, 20, 20), 1, now, now, ()),
    ]
    ambiguous = PoseObservation([(1, 1, 1)], (0, 0, 20, 20), 1, 1)
    assert pipeline._associate_poses([ambiguous], tracks) == []
    assert pipeline.get_status()["behavior"]["counters"]["ambiguous"] == 1
