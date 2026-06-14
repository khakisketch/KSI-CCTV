"""
VLM 추론 추상화 모듈

다양한 VLM 제공자(Ollama, LM Studio, Transformers)를 동일 인터페이스로 사용 가능.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class VLMAnalysisResult:
    """VLM 분석 결과"""
    description: str = ""
    risk_level: str = "low"  # low | medium | high | critical
    detected_events: list = field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 0.0
    inference_time_ms: float = 0.0
    raw_response: str = ""  # 원본 응답 (파싱 실패 시 디버깅용)
    parse_success: bool = True  # JSON 파싱 성공 여부

    def to_dict(self):
        return {
            "description": self.description,
            "risk_level": self.risk_level,
            "detected_events": self.detected_events,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "inference_time_ms": self.inference_time_ms,
            "parse_success": self.parse_success,
        }


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
        """NumPy RGB 배열을 JPEG bytes로 변환 후 분석.

        cv2.imencode는 BGR 채널 순서를 기대하므로 RGB 입력을 BGR로 변환한 뒤
        인코딩한다. (파이프라인은 BGR 프레임을 직접 imencode하므로 이 헬퍼를 쓰지
        않지만, 이름의 RGB 계약을 지켜 향후 오용 시 색 반전이 생기지 않게 한다.)
        """
        import cv2
        bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
        _, buffer = cv2.imencode(".jpg", bgr)
        return self.analyze(buffer.tobytes(), prompt)
