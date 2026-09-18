"""하드 크래시/강제종료로 pending으로 남은 VLM 검증의 시작 시 재조정.

graceful stop 경로는 파이프라인이 이미 FORCED_SHUTDOWN으로 확정하므로,
이 함수는 하드 크래시로 durable 저장소(`SQLiteEventRepository`)에
`verification_state="pending"`으로 남은 잔여분만 다룬다.
fail-open 원칙: 억제하지 않고 `unverified`로 보존한다(파이프라인
`SafetyPipeline._finalize_verification`의 unverified 매핑과 일치).
"""
from __future__ import annotations

from typing import Any


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def reconcile_pending_verifications(repository: Any, *, limit: int = 1000) -> int:
    """pending 잔여 검증을 unverified(RESTART_LOST)로 확정하고 건수를 반환한다.

    `repository.list(latest_only=True, ...)`의 각 payload 중
    `verification_state == "pending"`인 이벤트마다 동일 event_id로 새 revision을
    `record()`한다. 멱등: 재실행 시 pending이 없으므로 0을 반환한다.
    """
    effective_limit = min(int(limit), 1000)
    latest = repository.list(latest_only=True, limit=effective_limit)
    fixed = 0
    for payload in latest:
        if not isinstance(payload, dict):
            continue
        if payload.get("verification_state") != "pending":
            continue
        event_id = payload.get("event_id")
        if not event_id:
            continue
        revised = dict(payload)
        revised["verification_state"] = "unverified"
        revised["verification_reason_codes"] = ["RESTART_LOST"]
        revised["verification_summary"] = {
            "status": "unverified",
            "reason": "RESTART_LOST",
            "frames": _as_int(payload.get("frame_count")),
            "confirm": _as_int(payload.get("confirm_count")),
            "reject": _as_int(payload.get("reject_count")),
            "error": _as_int(payload.get("error_count")),
        }
        revised["alert_state"] = "unverified"
        repository.record(revised)
        fixed += 1
    return fixed
