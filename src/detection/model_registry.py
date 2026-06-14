"""다중 모델 레지스트리.

Phase 2 foundation: YAML model profile 기반으로 여러 YOLO ONNX 모델을
안전하게 로드하고 모델별 탐지 결과를 반환한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Type

import numpy as np

from src.detection.class_adapter import adapt_detections, get_class_map_for_profile
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
    profile: Dict[str, Any] = field(default_factory=dict)


class MultiModelRegistry:
    """여러 탐지 모델을 관리하고 모델별 추론 결과를 반환한다.

    - `enabled: true` 모델만 로드한다.
    - 현재 지원 type은 `yolo_onnx`만이다.
    - strict=False(기본)에서는 개별 모델 로드 실패가 전체 시스템 중단으로
      이어지지 않고 `load_status`에 기록된다.
    """

    SUPPORTED_TYPES = {"yolo_onnx"}

    def __init__(
        self,
        profiles: Dict[str, Any],
        detector_cls: Type[YOLODetector] = YOLODetector,
        strict: bool = False,
    ):
        self.detector_cls = detector_cls
        self.strict = strict
        self.profiles = self._extract_model_profiles(profiles)
        self.models: Dict[str, YOLODetector] = {}
        self.load_status: Dict[str, ModelLoadStatus] = {}

        self._load_enabled_models()

    @classmethod
    def from_config(
        cls,
        path: str = "./config/model_profiles.yaml",
        detector_cls: Type[YOLODetector] = YOLODetector,
        strict: bool = False,
    ) -> "MultiModelRegistry":
        """YAML 설정 파일에서 레지스트리 생성."""
        return cls(ConfigLoader.load(path), detector_cls=detector_cls, strict=strict)

    @staticmethod
    def _extract_model_profiles(config: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
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
                self.models[name] = self._create_yolo_detector(profile)
                status.loaded = True
                logger.info("모델 로드 완료: %s", name)
            except Exception as exc:
                status.error = str(exc)
                logger.error("모델 로드 실패 [%s]: %s", name, exc)
                if self.strict:
                    raise

    def _create_yolo_detector(self, profile: Dict[str, Any]) -> YOLODetector:
        input_size = tuple(profile.get("input_size", [640, 640]))
        return self.detector_cls(
            model_path=profile.get("model_path", ""),
            input_size=input_size,
            conf_threshold=float(profile.get("conf_threshold", 0.25)),
            iou_threshold=float(profile.get("iou_threshold", 0.45)),
            classes=profile.get("classes") or None,
            device=profile.get("device", "cuda"),
            fp16=bool(profile.get("fp16", True)),
            postprocess_mode=profile.get("postprocess_mode", "auto"),
            class_profile=profile.get("class_profile", "coco"),
        )

    @property
    def active_model_names(self) -> List[str]:
        """로드 완료된 모델 이름 목록."""
        return list(self.models.keys())

    @property
    def failed_models(self) -> Dict[str, str]:
        """로드 실패 또는 unsupported 모델의 오류 목록."""
        return {
            name: status.error
            for name, status in self.load_status.items()
            if status.enabled and not status.loaded and status.error
        }

    def get_model(self, name: str) -> Optional[YOLODetector]:
        return self.models.get(name)

    def detect_model(self, name: str, image: np.ndarray) -> List[Detection]:
        """특정 모델로 탐지."""
        model = self.get_model(name)
        if model is None:
            raise KeyError(f"Loaded model not found: {name}")
        detections = model.detect(image)
        profile = self.profiles.get(name, {})
        class_map = get_class_map_for_profile(profile)
        return adapt_detections(detections, class_map=class_map, model_name=name)

    def detect_all(
        self,
        image: np.ndarray,
        model_names: Optional[Iterable[str]] = None,
    ) -> Dict[str, List[Detection]]:
        """로드된 모델들을 실행하고 `{model_name: detections}`를 반환."""
        selected = list(model_names) if model_names is not None else self.active_model_names
        results: Dict[str, List[Detection]] = {}

        for name in selected:
            model = self.get_model(name)
            if model is None:
                logger.warning("로드되지 않은 모델 탐지 생략: %s", name)
                continue
            try:
                detections = model.detect(image)
                profile = self.profiles.get(name, {})
                class_map = get_class_map_for_profile(profile)
                results[name] = adapt_detections(
                    detections,
                    class_map=class_map,
                    model_name=name,
                )
            except Exception as exc:
                logger.error("모델 추론 실패 [%s]: %s", name, exc)
                if self.strict:
                    raise
                results[name] = []

        return results

    def to_status_dict(self) -> Dict[str, Dict[str, Any]]:
        """대시보드/헬스체크용 상태 dict."""
        return {
            name: {
                "enabled": status.enabled,
                "loaded": status.loaded,
                "error": status.error,
                "type": status.profile.get("type", ""),
                "model_path": status.profile.get("model_path", ""),
                "class_profile": status.profile.get("class_profile", ""),
                "class_map": status.profile.get("class_map"),
            }
            for name, status in self.load_status.items()
        }
