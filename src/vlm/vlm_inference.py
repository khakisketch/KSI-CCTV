"""
VLM 추론 추상화 모듈

다양한 VLM 제공자(Ollama, LM Studio, Transformers)를 동일 인터페이스로 사용 가능.
"""
from __future__ import annotations

import ipaddress
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class VerificationVerdict(str):
    """VLM/분류기 검증 결과 판정"""
    CONFIRM = "confirm"       # 이벤트 확정
    REJECT = "reject"         # 오탐(거짓 양성) — 알림 억제
    ABSTAIN = "abstain"       # 불확실 — 추가 검토 필요
    ERROR = "error"           # 분석 실패 — 억제하지 않음


@dataclass
class VLMAnalysisResult:
    """VLM 분석 결과"""
    description: str = ""
    risk_level: str = "low"  # low | medium | high | critical
    detected_events: list = field(default_factory=list)
    recommended_action: str = ""
    confidence: float = 0.0  # 0.0-1.0, 보정된 신뢰도
    inference_time_ms: float = 0.0
    raw_response: str = ""  # 원본 응답 (파싱 실패 시 디버깅용)
    parse_success: bool = True  # JSON 파싱 성공 여부
    verdict: str = VerificationVerdict.ABSTAIN  # 검증 판정
    # 실패 원인 구분. 빈 문자열이면 실패가 아니다.
    # provider_unavailable(연결/자격 실패) · timeout(마감 초과) · malformed(응답 파싱 실패)
    # · input_rejected(입력 한도 초과). 이게 없으면 provider 장애가 운영자에게
    # "응답 형식 오류"로 잘못 보고돼 엉뚱한 곳을 조사하게 된다.
    failure_kind: str = ""

    def to_dict(self):
        return {
            "description": self.description,
            "risk_level": self.risk_level,
            "detected_events": self.detected_events,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "inference_time_ms": self.inference_time_ms,
            "parse_success": self.parse_success,
            "verdict": self.verdict,
            "failure_kind": self.failure_kind,
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

    def analyze_frames(self, image_bytes: list[bytes], prompt: str) -> list[VLMAnalysisResult]:
        """Analyze ordered frames. Providers may override with a native batch request."""
        return [self.analyze(image, prompt) for image in image_bytes]

    def capabilities(self) -> dict[str, Any]:
        """Return the explicit runtime contract required by the pipeline."""
        return {"runtime_qualified": False, "model_digest": "", "max_images": 1,
                "max_total_bytes": 0}


def _allowed_vlm_host(host: str, allowed_hosts: list[str]) -> bool:
    normalized = host.strip().lower()
    configured = {item.strip().lower() for item in allowed_hosts if item.strip()}
    if configured and normalized not in configured:
        return False
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
        return bool(address.is_loopback or address.is_private)
    except ValueError:
        # Do not resolve names here and again in requests: that creates a DNS-rebinding
        # gap for CCTV frames. Qualified remote runtimes must use a private IP literal.
        return False


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
    digest = str(cfg.get("model_digest", "")).lower()
    if not bool(cfg.get("runtime_qualified", False)) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        logger.warning("VLM runtime is not qualified; disabling VLM")
        return None
    provider_name = str(cfg.get("provider", "ollama")).strip().lower()
    if provider_name in {"ollama", ""}:
        allowed_hosts = cfg.get("allowed_hosts", [])
        if not isinstance(allowed_hosts, list) or any(
            not isinstance(host, str) or not host.strip() for host in allowed_hosts
        ):
            logger.warning("VLM allowed_hosts must be a list of nonempty host names; disabling VLM")
            return None
        base_url = cfg.get("base_url", "http://localhost:11434")
        # base_url 스킴 검증 — CCTV 프레임(PII)을 의도치 않은 스킴/대상으로 전송하지 않도록.
        # (file://·gopher:// 등 SSRF성 스킴 차단. http/https만 허용.)
        from urllib.parse import urlparse
        parsed = urlparse(base_url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {"", "/"}
                or not _allowed_vlm_host(parsed.hostname, allowed_hosts)):
            logger.warning("VLM endpoint is not an approved local/internal host; disabling VLM")
            return None
        if parsed.hostname.lower() == "localhost":
            port = f":{parsed.port}" if parsed.port is not None else ""
            base_url = parsed._replace(netloc=f"127.0.0.1{port}").geturl().rstrip("/")
        from src.vlm.ollama_provider import OllamaProvider
        return OllamaProvider(
            base_url=base_url,
            model=cfg.get("model_name", "qwen3-vl:latest"),
            timeout_sec=float(cfg.get("timeout_sec", 60)),
            max_retries=int(cfg.get("max_retries", 2)),
            model_digest=digest,
            max_images=int(cfg.get("max_frames", 3)),
            max_total_bytes=int(cfg.get("max_total_bytes", 3 * 1024 * 1024)),
        )
    logger.warning(
        "알 수 없는 VLM provider '%s' — VLM 비활성(2차 분석 건너뜀). 지원: ollama.",
        provider_name,
    )
    return None
