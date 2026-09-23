"""P3-T1: 정기 보고서 LLM provider 추상화.

엔진을 provider 뒤에 격리한다. 실패(타임아웃·비정상·빈 응답)는 예외로
호출측에 전달하며, 호출측이 보고서 생성을 건너뛴다 (빈 보고서·환각 대체 금지).
"""

from __future__ import annotations

import abc
import ipaddress
from urllib.parse import urlparse

import requests


class ReportProvider(abc.ABC):
    """보고서 텍스트 생성 인터페이스."""

    @abc.abstractmethod
    def generate(self, prompt: str) -> str:
        """프롬프트로부터 보고서 텍스트를 생성한다. 실패 시 예외."""
        raise NotImplementedError


def _is_local_host(host: str) -> bool:
    """loopback/private 리터럴 또는 localhost만 허용 (VLM 선례 준수).

    DNS 이름은 재바인딩 간극 방지를 위해 거부한다 (localhost 제외).
    """
    normalized = host.strip().lower()
    if normalized == "localhost":
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    # 0.0.0.0/:: 같은 unspecified는 전 인터페이스 바인딩 의미이므로 거부한다.
    return bool((address.is_loopback or address.is_private) and not address.is_unspecified)


class OllamaTextProvider(ReportProvider):
    """Ollama `/api/chat` 기반 텍스트 생성 (loopback/private endpoint만)."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "",
        timeout_sec: float = 60.0,
    ) -> None:
        if not model or not model.strip():
            raise ValueError("report LLM model must be specified")
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or not _is_local_host(parsed.hostname)
        ):
            raise ValueError("report LLM endpoint must be loopback/private only")
        if parsed.hostname.lower() == "localhost":
            port = f":{parsed.port}" if parsed.port is not None else ""
            base_url = parsed._replace(netloc=f"127.0.0.1{port}").geturl()
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_sec = timeout_sec

    def generate(self, prompt: str) -> str:
        """Ollama chat 호출. 타임아웃·HTTP 오류·빈 응답은 예외로 전달."""
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
            },
            timeout=self.timeout_sec,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")
        text = str(resp.json().get("message", {}).get("content", "")).strip()
        if not text:
            raise RuntimeError("Ollama returned empty response")
        return text
