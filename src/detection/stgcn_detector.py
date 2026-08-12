"""Feature-gated ST-GCN ONNX action classification."""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import onnxruntime as ort

from src.detection.model_metrics import ModelMetrics
from src.detection.pose_models import ActionPrediction, PoseObservation
from src.detection.yolo_detector import _select_providers

logger = logging.getLogger(__name__)
_ORT_RUNTIME_ERROR = getattr(ort, "OrtRuntimeError", RuntimeError)


class STGCNOnnxClassifier:
    """Classify exactly one `[1,C,T,V,M]` pose sequence, with one person (`M=1`)."""

    def __init__(
        self,
        model_path: str | Path | None,
        class_names: Sequence[str],
        *,
        enabled: bool = False,
        keypoint_count: int = 17,
        sequence_length: int = 30,
        device: str = "auto",
    ) -> None:
        if not class_names or any(
            not isinstance(name, str) or not name.strip() for name in class_names
        ):
            raise ValueError("class_names must be non-empty strings")
        if keypoint_count <= 0 or sequence_length <= 0:
            raise ValueError("keypoint_count and sequence_length must be positive")
        self.enabled, self.model_path = bool(enabled), Path(model_path) if model_path else None
        self.class_names = tuple(name.strip() for name in class_names)
        self.keypoint_count, self.sequence_length, self.device = (
            int(keypoint_count),
            int(sequence_length),
            device,
        )
        self.session: ort.InferenceSession | None = None
        self.input_name, self.error = "", None
        self.input_dtype = np.float32
        self.metrics = ModelMetrics(name="stgcn_onnx")
        if self.enabled:
            self._init_session()

    @property
    def available(self) -> bool:
        return self.enabled and self.session is not None

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "error": self.error,
            **self.metrics.to_dict(),
        }

    def _init_session(self) -> None:
        if self.model_path is None or not self.model_path.is_file():
            self.error = "ST-GCN model is not configured or does not exist"
            return
        try:
            self.session = ort.InferenceSession(
                str(self.model_path),
                providers=_select_providers(self.device, ort.get_available_providers()),
            )
            meta = self.session.get_inputs()[0]
            self.input_name = meta.name
            self.input_dtype = np.float16 if "float16" in meta.type else np.float32
        except Exception as exc:
            self.session = None
            self.error = f"ST-GCN ONNX session initialization failed: {exc}"
            logger.warning("%s", self.error)

    def classify(self, observations: Sequence[PoseObservation]) -> ActionPrediction | None:
        if not self.available:
            return None
        started = time.perf_counter()
        try:
            tensor = self.prepare_input(observations)
            assert self.session is not None
            prediction = self._decode_output(self.session.run(None, {self.input_name: tensor})[0])
        except (ValueError, IndexError, TypeError, _ORT_RUNTIME_ERROR) as exc:
            self.error = f"ST-GCN inference failed: {exc}"
            self.metrics.record_error()
            logger.warning("%s", self.error)
            return None
        except Exception as exc:
            self.error = f"ST-GCN inference failed: {exc}"
            self.metrics.record_error()
            logger.exception("%s", self.error)
            return None
        self.error = None
        self.metrics.record_success((time.perf_counter() - started) * 1000.0)
        return prediction

    def prepare_input(self, observations: Sequence[PoseObservation]) -> np.ndarray:
        if len(observations) != self.sequence_length:
            raise ValueError(f"expected exactly {self.sequence_length} observations")
        data = np.empty((1, 3, self.sequence_length, self.keypoint_count, 1), dtype=np.float32)
        for frame_index, observation in enumerate(observations):
            points = np.asarray(observation.keypoints, dtype=np.float32)
            if points.shape != (self.keypoint_count, 3) or not np.isfinite(points).all():
                raise ValueError(f"observation {frame_index} has invalid keypoints")
            x1, y1, x2, y2 = observation.bbox
            width, height = x2 - x1, y2 - y1
            if width <= 0 or height <= 0:
                raise ValueError("observation bbox must have positive dimensions")
            data[0, 0, frame_index, :, 0] = (points[:, 0] - x1) / width
            data[0, 1, frame_index, :, 0] = (points[:, 1] - y1) / height
            data[0, 2, frame_index, :, 0] = points[:, 2]
        if not np.isfinite(data).all():
            raise ValueError("normalized ST-GCN input is not finite")
        return data.astype(self.input_dtype, copy=False)

    def _decode_output(self, output: Any) -> ActionPrediction:
        values = np.asarray(output, dtype=np.float64)
        if values.shape == (1, len(self.class_names)):
            values = values[0]
        if values.shape != (len(self.class_names),) or not np.isfinite(values).all():
            raise ValueError("ST-GCN output must be a finite [classes] or [1,classes] vector")
        if np.all((values >= 0.0) & (values <= 1.0)) and np.isclose(values.sum(), 1.0, atol=1e-5):
            probabilities = values
        else:
            shifted = values - np.max(values)
            exp_values = np.exp(shifted)
            probabilities = exp_values / exp_values.sum()
        class_index = int(np.argmax(probabilities))
        return ActionPrediction(
            self.class_names[class_index],
            class_index,
            float(probabilities[class_index]),
            tuple(probabilities),
        )
