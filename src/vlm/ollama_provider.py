"""
Ollama 기반 VLM 제공자

Qwen3-VL 등 Ollama 호환 모델을 HTTP API로 호출.

Phase 9: 메트릭 추적 (total_calls, success/failure, latency, last_error)
"""
from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Any

import requests

from .vlm_inference import VLMAnalysisResult, VLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(VLMProvider):
    """Ollama HTTP API를 통한 VLM 분석"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3-vl:latest",
        timeout_sec: float = 60.0,
        max_retries: int = 2,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec
        self.max_retries = max_retries
        # Phase 9: 메트릭
        self._metrics_lock = threading.Lock()
        self._metrics: dict[str, Any] = {
            "total_calls": 0,
            "success_count": 0,
            "failure_count": 0,
            "total_latency_ms": 0.0,
            "max_latency_ms": 0.0,
            "last_latency_ms": 0.0,
            "last_error": "",
            "last_risk_level": "",
        }

    # ───────── Phase 9: Metrics ─────────

    def get_metrics(self) -> dict[str, Any]:
        """현재까지의 VLM 메트릭을 반환 (thread-safe snapshot)."""
        with self._metrics_lock:
            m = dict(self._metrics)
        total = m["total_calls"]
        m["avg_latency_ms"] = round(m["total_latency_ms"] / total, 1) if total > 0 else 0.0
        m["success_rate"] = round(m["success_count"] / total, 3) if total > 0 else 0.0
        return m

    def reset_metrics(self) -> None:
        """메트릭 초기화."""
        with self._metrics_lock:
            self._metrics = {
                "total_calls": 0,
                "success_count": 0,
                "failure_count": 0,
                "total_latency_ms": 0.0,
                "max_latency_ms": 0.0,
                "last_latency_ms": 0.0,
                "last_error": "",
                "last_risk_level": "",
            }

    def _record_success(self, latency_ms: float, risk_level: str) -> None:
        with self._metrics_lock:
            m = self._metrics
            m["total_calls"] += 1
            m["success_count"] += 1
            m["total_latency_ms"] += latency_ms
            m["last_latency_ms"] = latency_ms
            if latency_ms > m["max_latency_ms"]:
                m["max_latency_ms"] = latency_ms
            m["last_risk_level"] = risk_level
            m["last_error"] = ""

    def _record_failure(self, latency_ms: float, error: str) -> None:
        with self._metrics_lock:
            m = self._metrics
            m["total_calls"] += 1
            m["failure_count"] += 1
            m["total_latency_ms"] += latency_ms
            m["last_latency_ms"] = latency_ms
            if latency_ms > m["max_latency_ms"]:
                m["max_latency_ms"] = latency_ms
            m["last_error"] = error[:200]

    def is_available(self) -> bool:
        """Ollama 서비스 연결 가능 여부 확인"""
        try:
            resp = requests.get(
                f"{self.base_url}/api/tags",
                timeout=5.0,
            )
            if resp.status_code != 200:
                return False
            models = resp.json().get("models", [])
            model_names = [m.get("name", "") for m in models]
            # qwen3-vl:latest 또는 qwen3-vl 같은 이름으로 매칭
            target_base = self.model.split(":")[0]
            return any(target_base in name for name in model_names)
        except Exception as e:
            logger.debug(f"Ollama 연결 확인 실패: {e}")
            return False

    def analyze(self, image_bytes: bytes, prompt: str) -> VLMAnalysisResult:
        """이미지 + 프롬프트로 Ollama API 호출"""
        start = time.time()

        # 이미지 base64 인코딩
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.1,
            },
        }

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout_sec,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")

                data = resp.json()
                content = data.get("message", {}).get("content", "").strip()
                inference_time_ms = (time.time() - start) * 1000

                result = self._parse_response(content)
                result.inference_time_ms = round(inference_time_ms, 1)
                # 메트릭 기록 (성공)
                self._record_success(inference_time_ms, result.risk_level)
                return result

            except Exception as e:
                last_error = e
                logger.warning(
                    f"Ollama 분석 실패 (시도 {attempt}/{self.max_retries}): {e}"
                )
                if attempt < self.max_retries:
                    time.sleep(1.0)

        # 모든 재시도 실패 시 폴백 결과 반환
        inference_time_ms = (time.time() - start) * 1000
        logger.error(f"VLM 분석 최종 실패: {last_error}")
        # 메트릭 기록 (실패)
        self._record_failure(inference_time_ms, str(last_error))
        return VLMAnalysisResult(
            description="VLM 분석 불가",
            risk_level="low",
            inference_time_ms=round(inference_time_ms, 1),
        )

    @staticmethod
    def _extract_json_object(text: str) -> str | None:
        """본문에서 첫 번째 완결된 {...} JSON 객체를 괄호 균형 매칭으로 추출.

        정규식으로 '{' 위치를 찾은 뒤 괄호 카운터로 닫는 위치를 탐색한다.
        이중 따옴표 내부 괄호는 이스케이프까지 고려해 무시한다.
        """
        # 본문 내 첫 '{' 시작 위치 탐색 (앞뒤 설명 텍스트 건너뜀)
        start = text.find("{")
        if start == -1:
            return None

        depth = 0
        in_string = False
        escape_next = False

        for i, ch in enumerate(text[start:], start=start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None

    def _parse_response(self, content: str) -> VLMAnalysisResult:
        """Ollama 응답에서 JSON 파싱.

        시도 순서:
          1. ```json ... ``` 코드 펜스 블록
          2. 본문 전체 json.loads
          3. 정규식/괄호 균형 매칭으로 첫 {...} 추출 (fallback)
          4. 모두 실패 시 parse_success=False, risk_level 보존 없이 'low' 유지하고
             원본 응답을 description에 담아 상위에서 판단 가능하게 한다.
        """
        raw_excerpt = content[:1000]

        def _build_result(data: dict) -> VLMAnalysisResult:
            return VLMAnalysisResult(
                description=data.get("description", ""),
                risk_level=data.get("risk_level", "low"),
                detected_events=data.get("detected_events", []),
                recommended_action=data.get("recommended_action", ""),
                confidence=float(data.get("confidence", 0.5)),
                raw_response=raw_excerpt,
                parse_success=True,
            )

        # ── 경로 1: ```json ... ``` 코드 펜스 블록 추출 ──
        if "```" in content:
            parts = content.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    return _build_result(json.loads(part))
                except (json.JSONDecodeError, ValueError):
                    continue

        # ── 경로 2: 본문 전체 json.loads ──
        try:
            return _build_result(json.loads(content))
        except (json.JSONDecodeError, ValueError):
            pass

        # ── 경로 3: 본문 내 첫 {...} 객체 추출 fallback ──
        # VLM이 "분석 결과: {...}" 형태로 응답하는 경우를 처리
        candidate = self._extract_json_object(content)
        if candidate:
            try:
                return _build_result(json.loads(candidate))
            except (json.JSONDecodeError, ValueError):
                pass

        # ── 경로 4: 모든 파싱 실패 ──
        # risk_level을 임의로 medium 고정하지 않는다 — 원본 텍스트를 보존해
        # 상위 레이어(pipeline/dashboard)가 맥락을 볼 수 있게 한다.
        logger.warning(
            "VLM 응답 JSON 파싱 실패 — 원본 텍스트를 description으로 보존. "
            "raw_response를 확인하세요."
        )
        return VLMAnalysisResult(
            description=content[:500],
            risk_level="low",  # 파싱 불가 = 위험 과대 판정 방지, 상위가 재판정
            raw_response=raw_excerpt,
            parse_success=False,
        )
