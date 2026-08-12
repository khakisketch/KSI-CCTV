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

from .vlm_inference import VerificationVerdict, VLMAnalysisResult, VLMProvider

logger = logging.getLogger(__name__)


class OllamaProvider(VLMProvider):
    """Ollama HTTP API를 통한 VLM 분석"""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        model: str = "qwen3-vl:latest",
        timeout_sec: float = 60.0,
        max_retries: int = 2,
        model_digest: str = "",
        max_images: int = 3,
        max_total_bytes: int = 3 * 1024 * 1024,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec
        # 0은 일반적으로 "재시도 없음"을 뜻하므로 최초 요청 1회는 항상 수행한다.
        self.max_retries = max(1, int(max_retries))
        self.model_digest = model_digest.lower()
        self.max_images = max(1, int(max_images))
        self.max_total_bytes = max(1, int(max_total_bytes))
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
            "timeout_count": 0, "malformed_count": 0, "provider_down_count": 0,
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
                "timeout_count": 0,
                "malformed_count": 0,
                "provider_down_count": 0,
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
            for item in models:
                if item.get("name") != self.model:
                    continue
                actual_digest = str(item.get("digest", "")).lower().removeprefix("sha256:")
                return not self.model_digest or actual_digest == self.model_digest
            return False
        except Exception as exc:
            logger.debug("Ollama availability check failed: %s", type(exc).__name__)
            return False

    def analyze(self, image_bytes: bytes, prompt: str) -> VLMAnalysisResult:
        if self.model_digest and not self.is_available():
            return self._qualification_failure()
        return self._analyze_one(image_bytes, prompt, time.monotonic() + self.timeout_sec)

    def _qualification_failure(self) -> VLMAnalysisResult:
        """Fail closed when the served model no longer matches the qualified digest."""
        self._record_failure(0.0, "ModelQualificationMismatch")
        with self._metrics_lock:
            self._metrics["provider_down_count"] += 1
        logger.error("Ollama model qualification check failed before inference")
        return VLMAnalysisResult(
            description="VLM 모델 자격 확인 실패",
            parse_success=False,
            verdict=VerificationVerdict.ERROR,
        )

    def capabilities(self) -> dict[str, Any]:
        return {"runtime_qualified": bool(self.model_digest), "model_digest": self.model_digest,
                "max_images": self.max_images, "max_total_bytes": self.max_total_bytes}

    def analyze_frames(self, image_bytes: list[bytes], prompt: str) -> list[VLMAnalysisResult]:
        """Analyze bounded ordered frames within one total deadline."""
        if not image_bytes or len(image_bytes) > self.max_images or sum(map(len, image_bytes)) > self.max_total_bytes:
            return [VLMAnalysisResult(description="VLM input limit exceeded", parse_success=False,
                                      verdict=VerificationVerdict.ERROR)]
        if self.model_digest and not self.is_available():
            return [self._qualification_failure() for _ in image_bytes]
        deadline = time.monotonic() + self.timeout_sec
        return [self._analyze_one(image, prompt, deadline) for image in image_bytes]

    def _analyze_one(self, image_bytes: bytes, prompt: str, deadline: float) -> VLMAnalysisResult:
        """Send one frame, sharing the caller's total deadline across retries."""
        start = time.monotonic()
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
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("VLM request deadline exceeded")
                resp = requests.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=remaining,
                )
                if resp.status_code != 200:
                    raise RuntimeError(f"Ollama HTTP {resp.status_code}: {resp.text[:200]}")

                data = resp.json()
                content = data.get("message", {}).get("content", "").strip()
                inference_time_ms = (time.monotonic() - start) * 1000

                result = self._parse_response(content)
                result.inference_time_ms = round(inference_time_ms, 1)
                # 메트릭 기록 (성공)
                self._record_success(inference_time_ms, result.risk_level)
                return result

            except Exception as e:
                last_error = e
                logger.warning("Ollama analysis failed (%d/%d): %s", attempt, self.max_retries, type(e).__name__)
                if attempt < self.max_retries:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(1.0, remaining))

        # 모든 재시도 실패 시 폴백 결과 반환
        inference_time_ms = (time.monotonic() - start) * 1000
        logger.error("VLM analysis failed: %s", type(last_error).__name__)
        # 메트릭 기록 (실패)
        self._record_failure(inference_time_ms, type(last_error).__name__)
        if isinstance(last_error, (TimeoutError, requests.Timeout)):
            with self._metrics_lock:
                self._metrics["timeout_count"] += 1
        with self._metrics_lock:
            self._metrics["provider_down_count"] += 1
        return VLMAnalysisResult(
            description="VLM 분석 불가 (네트워크/타임아웃/서버 오류)",
            risk_level="low",
            inference_time_ms=round(inference_time_ms, 1),
            parse_success=False,
            verdict=VerificationVerdict.ERROR,
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
        """Ollama 응답에서 JSON 파싱 및 엄격한 스키마 검증.

        시도 순서:
          1. ```json ... ``` 코드 펜스 블록
          2. 본문 전체 json.loads
          3. 정규식/괄호 균형 매칭으로 첫 {...} 추출 (fallback)
          4. 모두 실패 시 parse_success=False, verdict=ERROR 반환

        스키마 검증:
          - risk_level: low|medium|high|critical 중 하나
          - confidence: 0.0-1.0 범위
          - verdict: confirm|reject|abstain|error 중 하나 (없으면 ABSTAIN)
          - description: 비어 있지 않은 문자열
          - detected_events: 문자열 리스트
          - recommended_action: 문자열
        """
        raw_excerpt = content[:1000]

        VALID_RISK_LEVELS = {"low", "medium", "high", "critical"}
        VALID_VERDICTS = {"confirm", "reject", "abstain", "error"}

        def _validate_and_build(data: dict) -> VLMAnalysisResult:
            """엄격한 스키마 검증 후 결과 객체 생성"""
            if not isinstance(data, dict):
                raise ValueError("JSON 최상위 값은 객체여야 함")
            # 필수 필드 검증
            description = data.get("description")
            if not isinstance(description, str) or not description.strip():
                raise ValueError("description 필수")
            description = description.strip()

            risk_level = data.get("risk_level")
            if not isinstance(risk_level, str):
                raise ValueError("risk_level 필수")
            risk_level = risk_level.strip().lower()
            if risk_level not in VALID_RISK_LEVELS:
                raise ValueError(f"유효하지 않은 risk_level: {risk_level}")

            if "confidence" not in data:
                raise ValueError("confidence 필수")
            confidence = data["confidence"]
            try:
                confidence = float(confidence)
            except (TypeError, ValueError) as exc:
                raise ValueError("confidence는 숫자여야 함") from exc
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("confidence 범위는 0.0-1.0")

            verdict = data.get("verdict")
            if not isinstance(verdict, str):
                raise ValueError("verdict 필수")
            verdict = verdict.strip().lower()
            if verdict not in VALID_VERDICTS:
                raise ValueError(f"유효하지 않은 verdict: {verdict}")

            detected_events = data.get("detected_events", [])
            if not isinstance(detected_events, list):
                detected_events = []
            detected_events = [str(e).strip() for e in detected_events if str(e).strip()]

            recommended_action = data.get("recommended_action", "")
            if not isinstance(recommended_action, str):
                recommended_action = ""
            recommended_action = recommended_action.strip()

            return VLMAnalysisResult(
                description=description,
                risk_level=risk_level,
                detected_events=detected_events,
                recommended_action=recommended_action,
                confidence=confidence,
                raw_response=raw_excerpt,
                parse_success=True,
                verdict=verdict,
            )

        # ── 경로 1: ```json ... ``` 코드 펜스 블록 추출 ──
        if "```" in content:
            parts = content.split("```")
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    part = part[4:].strip()
                try:
                    return _validate_and_build(json.loads(part))
                except (json.JSONDecodeError, ValueError):
                    continue

        # ── 경로 2: 본문 전체 json.loads ──
        try:
            return _validate_and_build(json.loads(content))
        except (json.JSONDecodeError, ValueError):
            pass

        # ── 경로 3: 본문 내 첫 {...} 객체 추출 fallback ──
        candidate = self._extract_json_object(content)
        if candidate:
            try:
                return _validate_and_build(json.loads(candidate))
            except (json.JSONDecodeError, ValueError):
                pass

        # ── 경로 4: 모든 파싱 실패 ──
        logger.warning(
            "VLM 응답 JSON 파싱/검증 실패 — 원본 텍스트를 description으로 보존. "
            "raw_response를 확인하세요."
        )
        with self._metrics_lock:
            self._metrics["malformed_count"] += 1
        return VLMAnalysisResult(
            description=content[:500],
            risk_level="low",
            raw_response=raw_excerpt,
            parse_success=False,
            verdict=VerificationVerdict.ERROR,
        )
