"""Immutable value objects exchanged by pose and action-model stages."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class PoseObservation:
    """A single pose expressed in original-image coordinates, without image data."""

    keypoints: tuple[tuple[float, float, float], ...]
    bbox: tuple[float, float, float, float]
    timestamp: float
    confidence: float

    def __post_init__(self) -> None:
        points = np.asarray(self.keypoints, dtype=np.float32)
        if points.ndim != 2 or points.shape[0] == 0 or points.shape[1] != 3:
            raise ValueError("keypoints must have shape [V, 3]")
        if not np.isfinite(points).all():
            raise ValueError("keypoints must be finite")
        bbox = np.asarray(self.bbox, dtype=np.float32)
        if bbox.shape != (4,) or not np.isfinite(bbox).all():
            raise ValueError("bbox must contain four finite values")
        x1, y1, x2, y2 = bbox
        if x2 <= x1 or y2 <= y1:
            raise ValueError("bbox must have positive width and height")
        if not np.isfinite(self.timestamp):
            raise ValueError("timestamp must be finite")
        if not np.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be finite and within [0, 1]")
        object.__setattr__(self, "keypoints", tuple(tuple(float(v) for v in row) for row in points))
        object.__setattr__(self, "bbox", tuple(float(v) for v in bbox))
        object.__setattr__(self, "timestamp", float(self.timestamp))
        object.__setattr__(self, "confidence", float(self.confidence))


@dataclass(frozen=True, slots=True)
class ActionPrediction:
    """Immutable classified action probabilities for one fixed pose sequence."""

    action: str
    class_index: int
    confidence: float
    probabilities: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.action, str) or not self.action.strip():
            raise ValueError("action must be a non-empty string")
        probabilities = np.asarray(self.probabilities, dtype=np.float32)
        if (
            probabilities.ndim != 1
            or not len(probabilities)
            or not np.isfinite(probabilities).all()
        ):
            raise ValueError("probabilities must be a non-empty finite vector")
        if np.any(probabilities < 0.0) or np.any(probabilities > 1.0):
            raise ValueError("probabilities must be within [0, 1]")
        if not np.isclose(float(probabilities.sum()), 1.0, atol=1e-5):
            raise ValueError("probabilities must sum to one")
        if not 0 <= self.class_index < len(probabilities):
            raise ValueError("class_index is outside probabilities")
        if not np.isfinite(self.confidence) or not np.isclose(
            self.confidence, probabilities[self.class_index], atol=1e-5
        ):
            raise ValueError("confidence must match the selected probability")
        object.__setattr__(self, "action", self.action.strip())
        object.__setattr__(self, "confidence", float(self.confidence))
        object.__setattr__(self, "probabilities", tuple(float(value) for value in probabilities))

    @property
    def class_name(self) -> str:
        """Compatibility-friendly name for consumers that use detector terminology."""
        return self.action
