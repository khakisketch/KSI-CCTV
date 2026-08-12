"""다중 모델 레지스트리.

Phase 2 foundation: YAML model profile 기반으로 여러 YOLO ONNX 모델을
안전하게 로드하고 모델별 탐지 결과를 반환한다.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.detection.class_adapter import adapt_detections, get_class_map_for_profile
from src.detection.model_metrics import ModelMetrics
from src.detection.pose_detector import PoseOnnxDetector
from src.detection.pose_models import ActionPrediction, PoseObservation
from src.detection.stgcn_detector import STGCNOnnxClassifier
from src.detection.yolo_detector import Detection, YOLODetector
from src.utils.config import ConfigLoader

logger = logging.getLogger(__name__)

@dataclass
class ModelLoadStatus:
    """모델 로드 상태."""

    name: str
    enabled: bool
    loaded: bool = False
    error: str = ""
    profile: dict[str, Any] = field(default_factory=dict)


class MultiModelRegistry:
    """여러 탐지 모델을 관리하고 모델별 추론 결과를 반환한다.

    - `enabled: true` 모델만 로드한다.
    - 지원 type: `yolo_onnx`(closed-set ONNX) · `open_vocab`(YOLO-World 텍스트 프롬프트, 학습 없이 탐지).
    - strict=False(기본)에서는 개별 모델 로드 실패가 전체 시스템 중단으로
      이어지지 않고 `load_status`에 기록된다.
    - 모델별 latency/error metrics는 `get_model_metrics()`로 조회한다.
    """

    SUPPORTED_TYPES = {"yolo_onnx", "open_vocab", "pose_onnx", "stgcn_onnx"}
    # 연속 실패 이 횟수를 초과하면 runtime_error를 load_status에 반영
    CONSECUTIVE_ERROR_THRESHOLD = 3

    def __init__(
        self,
        profiles: dict[str, Any],
        detector_cls: type[YOLODetector] = YOLODetector,
        strict: bool = False,
        require_gpu: bool = False,
        pose_detector_cls: type[PoseOnnxDetector] = PoseOnnxDetector,
        stgcn_classifier_cls: type[STGCNOnnxClassifier] = STGCNOnnxClassifier,
    ):
        self.detector_cls = detector_cls
        self.pose_detector_cls = pose_detector_cls
        self.stgcn_classifier_cls = stgcn_classifier_cls
        self.strict = strict
        self.require_gpu = require_gpu
        self.profiles = self._extract_model_profiles(profiles)
        self.models: dict[str, YOLODetector] = {}
        self.pose_detectors: dict[str, PoseOnnxDetector] = {}
        self.action_classifiers: dict[str, STGCNOnnxClassifier] = {}
        self.load_status: dict[str, ModelLoadStatus] = {}
        # 모델별 런타임 metrics
        self._model_metrics: dict[str, ModelMetrics] = {}

        self._load_enabled_models()

    @classmethod
    def from_config(
        cls,
        path: str = "./config/model_profiles.yaml",
        detector_cls: type[YOLODetector] = YOLODetector,
        strict: bool = False,
        require_gpu: bool = False,
        pose_detector_cls: type[PoseOnnxDetector] = PoseOnnxDetector,
        stgcn_classifier_cls: type[STGCNOnnxClassifier] = STGCNOnnxClassifier,
    ) -> MultiModelRegistry:
        """YAML 설정 파일에서 레지스트리 생성."""
        return cls(
            ConfigLoader.load(path), detector_cls=detector_cls,
            pose_detector_cls=pose_detector_cls, stgcn_classifier_cls=stgcn_classifier_cls,
            strict=strict, require_gpu=require_gpu,
        )

    @staticmethod
    def _extract_model_profiles(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """전체 config 또는 models dict를 모델 프로파일 dict로 정규화."""
        if not isinstance(config, dict):
            return {}
        models = config.get("models", config)
        return {
            str(name): dict(profile)
            for name, profile in models.items()
            if isinstance(profile, dict)
        }

    def _load_enabled_models(self) -> None:
        for name, profile in self.profiles.items():
            enabled = bool(profile.get("enabled", True))
            status = ModelLoadStatus(name=name, enabled=enabled, profile=dict(profile))
            self.load_status[name] = status
            # metrics는 enabled 모델에 대해서만 생성
            if enabled:
                self._model_metrics[name] = ModelMetrics(name=name)

            if not enabled:
                logger.info("모델 비활성화로 로드 생략: %s", name)
                continue

            model_type = str(profile.get("type", "yolo_onnx"))
            if model_type not in self.SUPPORTED_TYPES:
                status.error = f"unsupported model type: {model_type}"
                logger.warning("모델 로드 생략 [%s]: %s", name, status.error)
                if self.strict:
                    raise ValueError(status.error)
                continue

            try:
                if model_type == "open_vocab":
                    self.models[name] = self._create_openvocab_detector(profile)
                elif model_type == "yolo_onnx":
                    self.models[name] = self._create_yolo_detector(profile)
                elif model_type == "pose_onnx":
                    detector = self._create_pose_detector(profile)
                    self._register_behavior_model(name, detector)
                    self.pose_detectors[name] = detector
                else:
                    classifier = self._create_stgcn_classifier(profile)
                    self._register_behavior_model(name, classifier)
                    self.action_classifiers[name] = classifier
                status.loaded = True
                logger.info("모델 로드 완료: %s (type=%s)", name, model_type)
            except Exception as exc:
                status.error = str(exc)
                logger.error("모델 로드 실패 [%s]: %s", name, exc)
                if self.strict:
                    raise

    def _register_behavior_model(self, name: str, model: Any) -> None:
        error = getattr(model, "error", None)
        if not bool(getattr(model, "available", False)):
            raise RuntimeError(error or "model session is unavailable")
        model.metrics = self._model_metrics[name]

    @staticmethod
    def _positive_int(value: Any, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{field_name} must be a positive integer")
        return value

    def _create_pose_detector(self, profile: dict[str, Any]) -> PoseOnnxDetector:
        input_size = profile.get("input_size", [640, 640])
        if not isinstance(input_size, (list, tuple)) or len(input_size) != 2:
            raise ValueError("pose_onnx input_size must contain width and height")
        width = self._positive_int(input_size[0], "pose_onnx input_size width")
        height = self._positive_int(input_size[1], "pose_onnx input_size height")
        keypoint_count = self._positive_int(profile.get("keypoint_count", 17), "pose_onnx keypoint_count")
        confidence = profile.get("confidence_threshold", 0.25)
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
            raise ValueError("pose_onnx confidence_threshold must be within [0, 1]")
        output_layout = profile.get("output_layout", "bcn")
        if output_layout not in {"bcn", "bnc"}:
            raise ValueError("pose_onnx output_layout must be 'bcn' or 'bnc'")
        device = profile.get("device", "auto")
        if not isinstance(device, str) or not device:
            raise ValueError("pose_onnx device must be a non-empty string")
        return self.pose_detector_cls(
            profile.get("model_path"), enabled=True, input_size=(width, height),
            keypoint_count=keypoint_count, confidence_threshold=float(confidence),
            output_layout=output_layout, device=device,
        )

    def _create_stgcn_classifier(self, profile: dict[str, Any]) -> STGCNOnnxClassifier:
        class_names = profile.get("class_names", profile.get("classes"))
        if not isinstance(class_names, (list, tuple)) or not class_names or any(
            not isinstance(item, str) or not item.strip() for item in class_names
        ):
            raise ValueError("stgcn_onnx class_names or classes must be non-empty strings")
        keypoint_count = self._positive_int(profile.get("keypoint_count", 17), "stgcn_onnx keypoint_count")
        sequence_length = self._positive_int(profile.get("sequence_length", 30), "stgcn_onnx sequence_length")
        device = profile.get("device", "auto")
        if not isinstance(device, str) or not device:
            raise ValueError("stgcn_onnx device must be a non-empty string")
        return self.stgcn_classifier_cls(
            profile.get("model_path"), class_names, enabled=True,
            keypoint_count=keypoint_count, sequence_length=sequence_length, device=device,
        )

    def _create_yolo_detector(self, profile: dict[str, Any]) -> YOLODetector:
        input_size = tuple(profile.get("input_size", [640, 640]))
        return self.detector_cls(
            model_path=profile.get("model_path", ""),
            input_size=input_size,
            conf_threshold=float(profile.get("conf_threshold", 0.25)),
            iou_threshold=float(profile.get("iou_threshold", 0.45)),
            classes=profile.get("classes") or None,
            device=profile.get("device", "auto"),
            fp16=bool(profile.get("fp16", True)),
            postprocess_mode=profile.get("postprocess_mode", "auto"),
            class_profile=profile.get("class_profile", "coco"),
            require_gpu=self.require_gpu,
        )

    def _create_openvocab_detector(self, profile: dict[str, Any]):
        """open_vocab(YOLO-World) 탐지기 생성 — classes=프롬프트 목록(학습 없이 즉시 탐지).

        ultralytics는 lazy import(미설치면 이 모델만 로드 실패, 나머지 시스템은 정상).
        """
        from src.detection.openvocab_detector import DEFAULT_WEIGHTS, OpenVocabDetector

        return OpenVocabDetector(
            model_path=profile.get("model_path") or DEFAULT_WEIGHTS,
            classes=profile.get("classes") or [],
            conf_threshold=float(profile.get("conf_threshold", 0.05)),
            iou_threshold=float(profile.get("iou_threshold", 0.45)),
            device=profile.get("device", "auto"),
        )

    @property
    def active_model_names(self) -> list[str]:
        """로드 완료된 모델 이름 목록."""
        return [name for name, status in self.load_status.items() if status.loaded]

    @property
    def failed_models(self) -> dict[str, str]:
        """로드 실패 또는 unsupported 모델의 오류 목록."""
        return {
            name: status.error
            for name, status in self.load_status.items()
            if status.enabled and not status.loaded and status.error
        }

    def get_model(self, name: str) -> YOLODetector | None:
        return self.models.get(name)

    def detect_model(self, name: str, image: np.ndarray) -> list[Detection]:
        """특정 모델로 탐지. latency/error metrics를 기록한다."""
        model = self.get_model(name)
        if model is None:
            raise KeyError(f"Loaded model not found: {name}")
        metrics = self._model_metrics.get(name)
        t0 = time.perf_counter()
        try:
            detections = model.detect(image)
            latency_ms = (time.perf_counter() - t0) * 1000.0
            if metrics is not None:
                metrics.record_success(latency_ms)
            profile = self.profiles.get(name, {})
            class_map = get_class_map_for_profile(profile)
            return adapt_detections(detections, class_map=class_map, model_name=name)
        except Exception:
            if metrics is not None:
                metrics.record_error()
                self._update_runtime_health(name, metrics)
            raise

    def detect_all(
        self,
        image: np.ndarray,
        model_names: Iterable[str] | None = None,
    ) -> dict[str, list[Detection]]:
        """로드된 모델들을 실행하고 `{model_name: detections}`를 반환.

        각 모델의 wall-clock latency와 실패 횟수를 metrics에 누적하고,
        연속 실패가 임계값을 초과하면 load_status에 runtime_error를 기록한다.
        """
        # Behavior models are intentionally excluded: this return contract is detection-only.
        selected = list(model_names) if model_names is not None else list(self.models)
        results: dict[str, list[Detection]] = {}

        for name in selected:
            model = self.get_model(name)
            if model is None:
                logger.warning("로드되지 않은 모델 탐지 생략: %s", name)
                continue
            metrics = self._model_metrics.get(name)
            t0 = time.perf_counter()
            try:
                detections = model.detect(image)
                latency_ms = (time.perf_counter() - t0) * 1000.0
                if metrics is not None:
                    metrics.record_success(latency_ms)
                    logger.debug(
                        "모델 추론 완료 [%s]: %.1f ms, 탐지 수: %d",
                        name,
                        latency_ms,
                        len(detections),
                    )
                profile = self.profiles.get(name, {})
                class_map = get_class_map_for_profile(profile)
                results[name] = adapt_detections(
                    detections,
                    class_map=class_map,
                    model_name=name,
                )
            except Exception as exc:
                if metrics is not None:
                    metrics.record_error()
                    self._update_runtime_health(name, metrics)
                logger.error("모델 추론 실패 [%s]: %s", name, exc)
                if self.strict:
                    raise
                results[name] = []

        return results

    def detect_poses(
        self,
        image: np.ndarray,
        person_bboxes: Iterable[tuple[float, float, float, float]],
        model_name: str,
        timestamp: float,
    ) -> list[PoseObservation]:
        """Run one loaded pose model for each person box and combine its observations."""
        detector = self.pose_detectors.get(model_name)
        if detector is None:
            raise KeyError(f"Loaded pose model not found: {model_name}")
        observations: list[PoseObservation] = []
        for bbox in person_bboxes:
            observations.extend(detector.detect(image, timestamp, bbox))
        self._update_runtime_health(model_name, self._model_metrics[model_name])
        return observations

    def classify_action(
        self,
        sequence: Iterable[PoseObservation],
        model_name: str,
    ) -> ActionPrediction | None:
        """Classify one pose sequence with a loaded ST-GCN model."""
        classifier = self.action_classifiers.get(model_name)
        if classifier is None:
            raise KeyError(f"Loaded action model not found: {model_name}")
        prediction = classifier.classify(tuple(sequence))
        self._update_runtime_health(model_name, self._model_metrics[model_name])
        return prediction

    def _update_runtime_health(self, name: str, metrics: ModelMetrics) -> None:
        """연속 실패가 임계값을 초과하면 load_status에 runtime_error를 기록한다."""
        if metrics.consecutive_errors >= self.CONSECUTIVE_ERROR_THRESHOLD:
            status = self.load_status.get(name)
            if status is not None:
                runtime_err = (
                    f"연속 추론 실패 {metrics.consecutive_errors}회 "
                    f"(총 실패: {metrics.error_count})"
                )
                status.error = runtime_err
                logger.warning(
                    "모델 런타임 health 악화 [%s]: %s",
                    name,
                    runtime_err,
                )

    def get_model_metrics(self) -> dict[str, dict[str, Any]]:
        """모델별 latency/error metrics 반환.

        반환 형태: {model_name: {"inference_count": int, "success_count": int,
            "error_count": int, "consecutive_errors": int,
            "avg_latency_ms": float, "p50_latency_ms": float,
            "p95_latency_ms": float, "last_latency_ms": float}}

        pipeline.get_status() 또는 대시보드에서 소비한다.
        """
        return {name: m.to_dict() for name, m in self._model_metrics.items()}

    def to_status_dict(self) -> dict[str, dict[str, Any]]:
        """대시보드/헬스체크용 상태 dict.

        기존 필드(enabled, loaded, error, type, model_path, class_profile, class_map)에
        runtime metrics(inference_count, error_count, consecutive_errors,
        avg_latency_ms, p50_latency_ms, p95_latency_ms, last_latency_ms)를 포함한다.
        """
        result: dict[str, dict[str, Any]] = {}
        for name, status in self.load_status.items():
            entry: dict[str, Any] = {
                "enabled": status.enabled,
                "loaded": status.loaded,
                "error": status.error,
                "type": status.profile.get("type", ""),
                "model_path": status.profile.get("model_path", ""),
                "class_profile": status.profile.get("class_profile", ""),
                "class_map": status.profile.get("class_map"),
            }
            # metrics는 enabled 모델에만 존재
            metrics = self._model_metrics.get(name)
            if metrics is not None:
                entry.update(metrics.to_dict())
            result[name] = entry
        return result
