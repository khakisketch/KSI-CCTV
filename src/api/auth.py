"""엔진 API 인증 — fail-closed API 키(대시보드 fail-closed 정책과 동일 철학).

KSI_CCTV_API_KEY 환경변수가 미설정/'change-me'이면 보호 엔드포인트를 **거부**(503)한다.
설정돼 있으면 요청 헤더 X-API-Key를 timing-safe 비교(401). 헬스 liveness는 비인증 허용(프로브용).
"""
from __future__ import annotations

import hmac
import os

from fastapi import Header, HTTPException, status

_PLACEHOLDER = {"", "change-me"}


def _configured_key() -> str:
    return os.environ.get("KSI_CCTV_API_KEY", "").strip()


def check_api_key(key: str | None) -> bool:
    """WS 핸드셰이크용 키 검증(의존성 주입 불가능한 경로용).

    KSI_CCTV_API_KEY 미설정/'change-me'이면 fail-closed(False). 일치하면 True.
    """
    configured = _configured_key()
    if configured in _PLACEHOLDER:
        return False
    provided = (key or "").strip()
    return bool(provided) and hmac.compare_digest(provided, configured)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """보호 엔드포인트 의존성. 키 미설정이면 fail-closed(503), 불일치면 401."""
    configured = _configured_key()
    if configured in _PLACEHOLDER:
        # fail-closed — 운영자가 키를 설정하기 전엔 API를 잠근다(무인증 노출 방지).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="API 키 미설정 — KSI_CCTV_API_KEY 환경변수에 강력한 키를 설정한 뒤 사용하세요.",
        )
    provided = (x_api_key or "").strip()
    if not provided or not hmac.compare_digest(provided, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="유효하지 않은 API 키.",
            headers={"WWW-Authenticate": "ApiKey"},
        )
