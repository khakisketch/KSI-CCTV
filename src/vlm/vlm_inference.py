"""
VLM 추론 추상화 모듈

다양한 VLM 제공자(Ollama, LM Studio, Transformers)를 동일 인터페이스로 사용 가능.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class VLMAnalysisResult:
    """VLM 분석 결과"""
    description: str = ""
    risk_level: str = "low"  # low | medium | high | critical
    detected_events: list = None
    recommended_action: str = ""
    confidence: float = 0.0
    inference_time_ms: float = 0.0

    def __post_init__(self):
        if self.detected_events is None:
            self.detected_events = []


class VLMProvider(ABC):
    """VLM 제공자 추상 클래스"""

    @abstractmethod
    def analyze(self, image_bytes: bytes, prompt: str) -> VLMAnalysisResult:
        """이미지 + 프롬프트로 분석 수행"""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """VLM 서비스 연결 가능 여부 확인"""
        ...

    def analyze_with_image_array(
        self, image_rgb, prompt: str
    ) -> VLMAnalysisResult:
        """NumPy RGB 배열을 JPEG bytes로 변환 후 분석"""
        import cv2
        _, buffer = cv2.imencode(".jpg", image_rgb)
        return self.analyze(buffer.tobytes(), prompt)
