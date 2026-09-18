"""요청 행위자(actor) 컨텍스트 — R5a 감사 추적용.

콘솔(서버사이드)은 변경 요청에 `X-KSI-Actor` 헤더를 싣고(R5b), 엔진
미들웨어가 이를 요청 컨텍스트(ContextVar)로 캡처한다. `write_audit`는
이 값을 actor 필드로 기록하므로 기존 호출부(32곳) 수정이 불필요하다.

FastAPI sync 핸들러는 threadpool에서 실행되지만, anyio가 호출 시점의
컨텍스트를 복사하므로 미들웨어에서 설정한 값이 핸들러에 보인다.
"""
from __future__ import annotations

import contextlib
import re
from contextvars import ContextVar, Token

_ACTOR_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DEFAULT_ACTOR = "system"

_actor: ContextVar[str] = ContextVar("ksi_actor", default=_DEFAULT_ACTOR)


def sanitize_actor(value: object) -> str:
    """actor 후보를 정제한다. strip 후 허용 문자(`^[A-Za-z0-9._-]{1,64}$`)에
    맞지 않으면 `"system"`으로 폴백한다. 절대 예외를 던지지 않는다."""
    try:
        if not isinstance(value, str):
            return _DEFAULT_ACTOR
        text = value.strip()
        if not text or len(text) > 64:
            return _DEFAULT_ACTOR
        if _ACTOR_RE.match(text) is None:
            return _DEFAULT_ACTOR
        return text
    except Exception:
        return _DEFAULT_ACTOR


def set_actor(value: object) -> Token[str]:
    """현재 컨텍스트의 actor를 설정한다(내부 정제 후 저장)."""
    return _actor.set(sanitize_actor(value))


def reset_actor(token: Token[str]) -> None:
    """`set_actor`가 반환한 토큰으로 이전 값을 복원한다(미들웨어 정리용)."""
    with contextlib.suppress(Exception):
        _actor.reset(token)


def current_actor() -> str:
    """현재 컨텍스트의 actor를 반환한다. 미설정·오염 시 `"system"`."""
    try:
        value = _actor.get()
    except LookupError:
        return _DEFAULT_ACTOR
    if not isinstance(value, str) or _ACTOR_RE.match(value) is None:
        return _DEFAULT_ACTOR
    return value
