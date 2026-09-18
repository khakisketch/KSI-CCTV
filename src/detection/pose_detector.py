"""Feature-gated ONNX YOLO-pose inference."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort

from src.detection.model_metrics import ModelMetrics
from src.detection.pose_models import PoseObservation
from src.detection.yolo_detector import _select_providers

logger = logging.getLogger(__name__)
_ORT_RUNTIME_ERROR = getattr(ort, "OrtRuntimeError", RuntimeError)


class PoseOnnxDetector:
    """Decode configured YOLO-pose `[1,C,N]` or `[1,N,C]` outputs safely.

    `detect` accepts an optional original-image bbox; coordinates are inferred on that
    crop and restored to the full image.  Disabled or unavailable models return `[]`.
    """

    _LAYOUTS = {"bcn", "bnc"}

    def __init__(
        self,
        model_path: str | Path | None,
        *,
        enabled: bool = False,
        input_size: tuple[int, int] = (640, 640),
        keypoint_count: int = 17,
        confidence_threshold: float = 0.25,
        output_layout: str = "bcn",
        device: str = "auto",
    ) -> None:
        if input_size[0] <= 0 or input_size[1] <= 0 or keypoint_count <= 0:
            raise ValueError("input_size and keypoint_count must be positive")
        if output_layout not in self._LAYOUTS:
            raise ValueError("output_layout must be 'bcn' ([1,C,N]) or 'bnc' ([1,N,C])")
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be within [0, 1]")
        self.enabled = bool(enabled)
        self.model_path = Path(model_path) if model_path else None
        self.input_size = tuple(int(value) for value in input_size)
        self.keypoint_count = int(keypoint_count)
        self.confidence_threshold = float(confidence_threshold)
        self.output_layout = output_layout
        self.device = device
        self.session: ort.InferenceSession | None = None
        self.input_name = ""
        self.output_names: list[str] = []
        self.input_dtype: Any = np.float32
        self.error: str | None = None
        self.metrics = ModelMetrics(name="pose_onnx")
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
            self.error = "pose model is not configured or does not exist"
            logger.warning("Pose detector disabled: %s", self.error)
            return
        try:
            providers = _select_providers(self.device, ort.get_available_providers())
            self.session = ort.InferenceSession(str(self.model_path), providers=providers)
            input_meta = self.session.get_inputs()[0]
            self.input_name = input_meta.name
            self.input_dtype = np.float16 if "float16" in input_meta.type else np.float32
            self.output_names = [item.name for item in self.session.get_outputs()]
        except Exception as exc:
            self.session = None
            self.error = f"pose ONNX session initialization failed: {exc}"
            logger.warning("%s", self.error)

    def detect(
        self,
        image: np.ndarray,
        timestamp: float,
        bbox: tuple[float, float, float, float] | None = None,
    ) -> list[PoseObservation]:
        """Infer poses, returning no result for disabled, invalid, or failed inference."""
        if not self.available:
            return []
        started = time.perf_counter()
        try:
            crop, offset = self._crop(image, bbox)
            tensor, letterbox = self._preprocess(crop)
            assert self.session is not None
            output = self.session.run(self.output_names, {self.input_name: tensor})[0]
            observations = self._decode(output, timestamp, crop.shape[:2], offset, letterbox)
        except (ValueError, IndexError, TypeError, _ORT_RUNTIME_ERROR) as exc:
            self.error = f"pose inference failed: {exc}"
            self.metrics.record_error()
            logger.warning("%s", self.error)
            return []
        except Exception as exc:  # An inference extension must never stop base detection.
            self.error = f"pose inference failed: {exc}"
            self.metrics.record_error()
            logger.exception("%s", self.error)
            return []
        self.error = None
        self.metrics.record_success((time.perf_counter() - started) * 1000.0)
        return observations

    def _crop(
        self, image: np.ndarray, bbox: tuple[float, float, float, float] | None
    ) -> tuple[np.ndarray, tuple[int, int]]:
        if image.ndim != 3 or image.shape[2] != 3 or not np.isfinite(image).all():
            raise ValueError("image must be a finite HWC BGR image")
        height, width = image.shape[:2]
        if not height or not width:
            raise ValueError("image must not be empty")
        if bbox is None:
            return image, (0, 0)
        values = np.asarray(bbox, dtype=np.float32)
        if values.shape != (4,) or not np.isfinite(values).all():
            raise ValueError("bbox must contain four finite values")
        x1, y1, x2, y2 = values
        left, top = max(0, int(np.floor(x1))), max(0, int(np.floor(y1)))
        right, bottom = min(width, int(np.ceil(x2))), min(height, int(np.ceil(y2)))
        if right <= left or bottom <= top:
            raise ValueError("bbox does not intersect the image")
        return image[top:bottom, left:right], (left, top)

    def _preprocess(self, image: np.ndarray) -> tuple[np.ndarray, tuple[float, int, int]]:
        height, width = image.shape[:2]
        target_width, target_height = self.input_size
        scale = min(target_width / width, target_height / height)
        resized_width, resized_height = round(width * scale), round(height * scale)
        pad_left, pad_top = (
            (target_width - resized_width) // 2,
            (target_height - resized_height) // 2,
        )
        canvas = np.full((target_height, target_width, 3), 114, dtype=np.uint8)
        canvas[pad_top : pad_top + resized_height, pad_left : pad_left + resized_width] = (
            cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
        )
        tensor = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB).transpose(2, 0, 1)
        return np.expand_dims(tensor.astype(np.float32) / 255.0, 0).astype(self.input_dtype), (
            scale,
            pad_left,
            pad_top,
        )

    def _decode(
        self,
        output: Any,
        timestamp: float,
        crop_shape: tuple[int, int],
        offset: tuple[int, int],
        letterbox: tuple[float, int, int],
    ) -> list[PoseObservation]:
        values = np.asarray(output, dtype=np.float32)
        expected_channels = 5 + 3 * self.keypoint_count
        if values.ndim != 3 or values.shape[0] != 1 or not np.isfinite(values).all():
            raise ValueError("pose output must be a finite rank-3 tensor with batch size one")
        if self.output_layout == "bcn":
            if values.shape[1] != expected_channels:
                raise ValueError(f"expected [1,{expected_channels},N] pose output")
            rows = values[0].T
        else:
            if values.shape[2] != expected_channels:
                raise ValueError(f"expected [1,N,{expected_channels}] pose output")
            rows = values[0]
        scale, pad_left, pad_top = letterbox
        crop_height, crop_width = crop_shape
        origin_x, origin_y = offset
        results: list[PoseObservation] = []
        for row in rows:
            confidence = float(row[4])
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("pose confidence must be within [0, 1]")
            if confidence < self.confidence_threshold:
                continue
            cx, cy, width, height = row[:4]
            x1, y1 = (cx - width / 2 - pad_left) / scale, (cy - height / 2 - pad_top) / scale
            x2, y2 = (cx + width / 2 - pad_left) / scale, (cy + height / 2 - pad_top) / scale
            x1, x2 = np.clip((x1, x2), 0, crop_width)
            y1, y2 = np.clip((y1, y2), 0, crop_height)
            if x2 <= x1 or y2 <= y1:
                continue
            points = row[5:].reshape(self.keypoint_count, 3).copy()
            if np.any((points[:, 2] < 0.0) | (points[:, 2] > 1.0)):
                raise ValueError("keypoint confidences must be within [0, 1]")
            points[:, 0] = np.clip((points[:, 0] - pad_left) / scale, 0, crop_width) + origin_x
            points[:, 1] = np.clip((points[:, 1] - pad_top) / scale, 0, crop_height) + origin_y
            results.append(
                PoseObservation(
                    points,
                    (x1 + origin_x, y1 + origin_y, x2 + origin_x, y2 + origin_y),
                    timestamp,
                    confidence,
                )
            )
        return results
