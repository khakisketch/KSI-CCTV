"""Webhook 발송자 — HMAC-SHA256 서명 + payload v1 (P2-T1).

표준 라이브러리 hmac/hashlib + 기존 의존성 requests만 사용한다.
재시도·백오프·dead-letter·멱등은 outbox 계약을 재사용하므로 여기서 처리하지 않는다.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

import requests

from src.events.models import NotificationAttempt, WebhookEndpoint, validate_webhook_url

logger = logging.getLogger(__name__)

PAYLOAD_VERSION = "v1"


def compute_signature(secret: str, body: bytes) -> str:
    """HMAC-SHA256 hex 서명 — secret(utf-8) + raw body."""
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def build_webhook_payload(
    attempt: NotificationAttempt, message_type: str = "alert"
) -> dict[str, Any]:
    """Payload v1 스키마 조립 — message_type은 "alert"|"test"만 허용."""
    if message_type not in ("alert", "test"):
        raise ValueError(f"허용되지 않은 message_type: {message_type!r} (alert/test만)")
    event = dict(attempt.payload or {})
    return {
        "version": PAYLOAD_VERSION,
        "event_id": event.get("event_id", ""),
        "idempotency_key": attempt.idempotency_key,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "type": message_type,
        "event": event,
    }


def send_webhook(
    attempt: NotificationAttempt,
    endpoint: WebhookEndpoint,
    timeout_sec: float,
    message_type: str = "alert",
) -> bool:
    """Webhook 1회 발송 — 2xx면 True, 검증 실패 포함 모든 예외 경로는 False."""
    if not endpoint.enabled:
        logger.warning("비활성 엔드포인트 발송 스킵: %s", endpoint.id)
        return False
    try:
        validate_webhook_url(endpoint.url)
        payload = build_webhook_payload(attempt, message_type)
        raw_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        event = payload["event"]
        headers = {
            "Content-Type": "application/json",
            "X-KSI-Signature": compute_signature(endpoint.secret, raw_body),
            "X-KSI-Delivery": attempt.idempotency_key,
            "X-KSI-Event": str(event.get("event_type", "unknown")),
            "X-KSI-Timestamp": str(int(time.time())),
        }
        response = requests.post(endpoint.url, data=raw_body, headers=headers, timeout=timeout_sec)
    except Exception as e:
        logger.warning("webhook 발송 실패 [%s]: %s", endpoint.id, e)
        return False
    if 200 <= response.status_code < 300:
        return True
    logger.warning("webhook 비정상 응답 [%s]: HTTP %s", endpoint.id, response.status_code)
    return False


__all__ = [
    "PAYLOAD_VERSION",
    "build_webhook_payload",
    "compute_signature",
    "send_webhook",
    "validate_webhook_url",
]
