"""다중 모델 레지스트리.

Phase 2 foundation: YAML model profile 기반으로 여러 YOLO ONNX 모델을
안전하게 로드하고 모델별 탐지 결과를 반환한다.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.detection.class_adapter import adapt_detections, get_class_map_for_profile
from src.detection.yolo_detector import Detection, YOLODetector
from src.utils.config import ConfigLoader

logger = logging.getLogger(__name__)

# 최근 latency 샘플 보존 개수 (p50/p95 계산용)
_LATENCY_WINDOW = 100


@dataclass
class ModelLoadStatus:
    """모델 로드 상태."""

    name: str
    enabled: bool
    loaded: bool = False
    error: str = ""
    profile: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelMetrics:
    """모델별 런타임 추론 metrics.

    - inference_count: 총 추론 시도 횟수 (성공 + 실패)
    - success_count: 성공 횟수
    - error_count: 실패(예외) 횟수
    - consecutive_errors: 현재까지 연속 실패 횟수 (성공 시 0 리셋)
    - total_latency_ms: 성공 추론 누적 시간 (ms)
    - last_latency_ms: 마지막 성공 추론 latency (ms), 미추론이면 -1
    - _latency_window: 최근 latency 샘플 (p50/p95 계산용)
    """

    name: str
    inference_count: int = 0
    success_count: int = 0
    error_count: int = 0
    consecutive_errors: int = 0
    total_latency_ms: float = 0.0
    last_latency_ms: float = -1.0
    _latency_window: deque[float] = field(default_factory=lambda: deque(maxlen=_LATENCY_WINDOW))

    def record_success(self, latency_ms: float) -> None:
        """성공 추론 결과를 기록한다."""
        self.inference_count += 1
        self.success_count += 1
        self.consecutive_errors = 0
        self.total_latency_ms += latency_ms
        self.last_latency_ms = latency_ms
        self._latency_window.append(latency_ms)

    def record_error(self) -> None:
        """추론 실패를 기록한다."""
        self.inference_count += 1
        self.error_count += 1
        self.consecutive_errors += 1

    @property
    def avg_latency_ms(self) -> float:
        """성공 추론 평균 latency (ms). 미추론이면 -1."""
        if self.success_count == 0:
            return -1.0
        return self.total_latency_ms / self.success_count

    @property
    def p50_latency_ms(self) -> float:
        """최근 샘플 기준 p50 latency (ms). 샘플 없으면 -1."""
        samples = list(self._latency_window)
        if not samples:
            return -1.0
        sorted_s = sorted(samples)
        mid = len(sorted_s) // 2
        if len(sorted_s) % 2 == 0:
            return (sorted_s[mid - 1] + sorted_s[mid]) / 2.0
        return sorted_s[mid]

    @property
    def p95_latency_ms(self) -> float:
        """최근 샘플 기준 p95 latency (ms). 샘플 없으면 -1."""
        samples = list(self._latency_window)
        if not samples:
            return -1.0
        sorted_s = sorted(samples)
        idx = max(0, int(len(sorted_s) * 0.95) - 1)
        return sorted_s[idx]

    def to_dict(self) -> dict[str, Any]:
        """대시보드/파이프라인 노출용 dict."""
        return {
            "inference_count": self.inference_count,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "consecutive_errors": self.consecutive_errors,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "last_latency_ms": round(self.last_latency_ms, 2),
        }


class MultiModelRegistry:
    """여러 탐지 모델을 관리하고 모델별 추론 결과를 반환한다.

    - `enabled: true` 모델만 로드한다.
    - 지원 type: `yolo_onnx`(closed-set ONNX) · `open_vocab`(YOLO-World 텍스트 프롬프트, 학습 없이 탐지).
    - strict=False(기본)에서는 개별 모델 로드 실패가 전체 시스템 중단으로
      이어지지 않고 `load_status`에 기록된다.
    - 모델별 latency/error metrics는 `get_model_metrics()`로 조회한다.
    """

    SUPPORTED_TYPES = {"yolo_onnx", "open_vocab"}
    # 연속 실패 이 횟수를 초과하면 runtime_error를 load_status에 반영
    CONSECUTIVE_ERROR_THRESHOLD = 3

    def __init__(
        self,
        profiles: dict[str, Any],
        detector_cls: type[YOLODetector] = YOLODetector,
        strict: bool = False,
    ):
        self.detector_cls = detector_cls
        self.strict = strict
        self.profiles = self._extract_model_profiles(profiles)
        self.models: dict[str, YOLODetector] = {}
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
    ) -> MultiModelRegistry:
        """YAML 설정 파일에서 레지스트리 생성."""
        return cls(ConfigLoader.load(path), detector_cls=detector_cls, strict=strict)

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
                else:
                    self.models[name] = self._create_yolo_detector(profile)
                status.loaded = True
                logger.info("모델 로드 완료: %s (type=%s)", name, model_type)
            except Exception as exc:
                status.error = str(exc)
                logger.error("모델 로드 실패 [%s]: %s", name, exc)
                if self.strict:
                    raise

    def _create_yolo_detector(self, profile: dict[str, Any]) -> YOLODetector:
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
        return list(self.models.keys())

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
        selected = list(model_names) if model_names is not None else self.active_model_names
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
