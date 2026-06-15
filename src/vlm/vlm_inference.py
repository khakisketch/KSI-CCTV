"""
VLM 추론 추상화 모듈

다양한 VLM 제공자(Ollama, LM Studio, Transformers)를 동일 인터페이스로 사용 가능.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


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


def create_vlm_provider(vlm_config: dict[str, Any] | None) -> VLMProvider | None:
    """config(models.vlm)로부터 VLM provider를 생성한다.

    `provider` 키로 구현을 선택한다(현재 'ollama' 지원). `enabled: false`이거나 알 수 없는
    provider면 None을 반환한다 — VLM은 **선택적 컴포넌트**로, 없으면 2차 정밀분석을 건너뛰고
    1차 YOLO 탐지만으로 동작한다.

    기본은 **on-prem Ollama**다: CCTV 프레임에는 얼굴 등 PII가 포함되므로 외부 API로 전송하지
    않는 로컬 추론을 택한다(ADR-0001 "외부 SaaS 미사용", 클라우드 호스팅은 제품 비목표).
    다른 provider가 필요하면 VLMProvider를 구현하고 아래 분기를 추가한다.
    """
    cfg = vlm_config or {}
    if not bool(cfg.get("enabled", False)):
        return None
    provider_name = str(cfg.get("provider", "ollama")).strip().lower()
    if provider_name in {"ollama", ""}:
        from src.vlm.ollama_provider import OllamaProvider
        return OllamaProvider(
            base_url=cfg.get("base_url", "http://localhost:11434"),
            model=cfg.get("model_name", "qwen3-vl:latest"),
            timeout_sec=float(cfg.get("timeout_sec", 60)),
            max_retries=int(cfg.get("max_retries", 2)),
        )
    logger.warning(
        "알 수 없는 VLM provider '%s' — VLM 비활성(2차 분석 건너뜀). 지원: ollama.",
        provider_name,
    )
    return None
