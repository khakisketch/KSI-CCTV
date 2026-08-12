"""엔진 API 데이터 접근 계층 — 기존 SSOT(config/이벤트/모델/정책)를 읽어 안전하게 반환.

라우트와 분리해 단위 테스트 가능하게 한다. 모든 외부 노출 텍스트는 시크릿 마스킹(mask_text/mask_url)을
거친다 — RTSP/SMTP 자격증명·VLM 자유서술 PII가 API로 새지 않게(보안 하드닝 보존).
"""
from __future__ import annotations

import asyncio
import base64
import collections
import fcntl
import hashlib
import json
import logging
import os
import re
import shutil
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from ruamel.yaml import YAML as _RuamelYAML

from src.alerts.alert_manager import parse_alert_log_line
from src.release_capabilities import release_qualification
from src.utils.config import mask_text, mask_url
from src.utils.health import ComponentHealth

_ROOT = Path(__file__).resolve().parents[2]
_MODEL_PROFILES = _ROOT / "config" / "model_profiles.yaml"
_EVENT_POLICIES = _ROOT / "config" / "event_policies.yaml"
_CAPTURES_DIR = _ROOT / "data" / "captures"
_ALERT_PREFS = _ROOT / "config" / "alert_preferences.yaml"
_ALERT_LEVELS = ("low", "medium", "high", "critical")
_CAMERA_PROFILES = _ROOT / "config" / "camera_profiles.yaml"
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD
_SAFE_SEG = re.compile(r"^[A-Za-z0-9._-]+$")  # 카메라/파일명(슬래시·.. 불허)
# 감사 로그(JSONL) — Streamlit 대시보드와 동일 파일을 공유해 단일 감사 트레일을 만든다.
_AUDIT_LOG = _ROOT / "logs" / "dashboard_audit.jsonl"
_AUDIT_MAX_BYTES = 5 * 1024 * 1024
_AUDIT_MAX_SEGMENTS = 8
_AUDIT_SECRET_KEY = re.compile(
    r"(?:pass(?:word|wd)?|secret|token|api[_-]?key|auth(?:orization)?|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)
_AUDIT_SECRET_VALUE = re.compile(
    r"(?:\b(?:password|passwd|secret|token|api[_-]?key|authorization|credential|cookie)\s*[:=]\s*\S+"
    r"|\bbearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----)",
    re.IGNORECASE,
)
_ALERTS_LOG = _ROOT / "logs" / "alerts.log"
logger = logging.getLogger(__name__)

EVENTS_MAX_LIMIT = 1000

# 자유서술/식별 텍스트만 마스킹 대상(구조·수치 필드는 보존 — 오탐 치환 방지).
_EVENT_TEXT_FIELDS = ("description", "vlm_raw_response", "vlm_recommended_action", "rule_name")


class EventLookupUnavailable(RuntimeError):
    """Raised when neither durable nor legacy event evidence can be read."""


class DurableEventStoreStatus:
    """Thread-safe health state for independent durable query and write paths."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, str] = {}
        self._last_failure_operation: str | None = None

    def record_failure(self, error: Exception, *, operation: str = "query") -> None:
        # Error messages may contain event payloads or connection secrets. Keep only
        # the exception type in state and structured process logging.
        error_type = type(error).__name__
        with self._lock:
            self._failures[operation] = error_type
            self._last_failure_operation = operation
        logger.warning(
            "durable event store operation failed",
            extra={"operation": operation, "error_type": error_type},
        )

    def record_success(self, *, operation: str = "query") -> None:
        with self._lock:
            self._failures.pop(operation, None)
            if self._last_failure_operation == operation:
                self._last_failure_operation = next(iter(self._failures), None)

    def health_check(self) -> ComponentHealth:
        with self._lock:
            failures = dict(self._failures)
            last_operation = self._last_failure_operation
        degraded = bool(failures)
        error_type = failures.get(last_operation) if last_operation is not None else None
        return ComponentHealth(
            name="durable_event_store",
            healthy=not degraded,
            message=(
                "durable event store has failed operations; inspect fallback and persistence logs"
                if degraded
                else "durable event store query and write paths healthy"
            ),
            metadata={
                "degraded": degraded,
                "last_error_type": error_type,
                "failed_operations": sorted(failures),
            },
        )


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ── ruamel round-trip(주석 보존) — 설정 쓰기 경로 전용 ─────────────────────────────
# 쓰기 setter가 _load_yaml_rt로 읽고 _write_yaml로 쓰면 파일의 주석(SMTP 안내·라이선스 경고 등)이
# 보존된다(기존 safe_dump는 주석을 제거했음 — 알려진 한계 해소). 읽기 전용 경로는 _load_yaml 유지.
_yaml_rt = _RuamelYAML()
_yaml_rt.preserve_quotes = True
_yaml_rt.indent(mapping=2, sequence=4, offset=2)
_yaml_rt.width = 4096  # 긴 값 줄바꿈 방지


def _load_yaml_rt(path: Path):
    """주석 보존 round-trip 로드(CommentedMap). 없거나 실패 시 빈 CommentedMap."""
    from ruamel.yaml.comments import CommentedMap

    if not path.exists():
        return CommentedMap()
    try:
        with open(path, encoding="utf-8") as f:
            data = _yaml_rt.load(f)
        return data if isinstance(data, dict) else CommentedMap()
    except Exception:
        return CommentedMap()


def _parse_iso_timestamp(ts: str | None) -> datetime | None:
    """ISO8601/Z 타임스탬프를 datetime으로 파싱. 실패 시 None."""
    if not isinstance(ts, str) or not ts:
        return None
    try:
        s = ts.strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(s)
        return (
            parsed.replace(tzinfo=timezone.utc)
            if parsed.tzinfo is None
            else parsed.astimezone(timezone.utc)
        )
    except Exception:
        return None


def _event_matches(
    ev: dict[str, Any],
    event_type: str | None,
    camera_id: str | None,
    alert_level: str | None,
    since_dt: datetime | None,
    until_dt: datetime | None,
) -> bool:
    """AND 조건 필터. timestamp 파싱 불가 이벤트는 since/until 비교에서 건너뛴다(제외하지 않음)."""
    if event_type and ev.get("event_type") != event_type:
        return False
    if camera_id and ev.get("camera_id") != camera_id:
        return False
    if alert_level and ev.get("alert_level") != alert_level:
        return False
    ev_ts_raw = ev.get("timestamp")
    ev_dt = _parse_iso_timestamp(ev_ts_raw) if isinstance(ev_ts_raw, str) else None
    if since_dt is not None and (ev_dt is None or ev_dt < since_dt):
        return False
    return not (until_dt is not None and (ev_dt is None or ev_dt > until_dt))


def list_events(
    limit: int = 100,
    event_type: str | None = None,
    camera_id: str | None = None,
    alert_level: str | None = None,
    since: str | None = None,
    until: str | None = None,
    offset: int = 0,
    alerts_log: Path | None = None,
    event_repository: Any | None = None,
    durable_store_status: DurableEventStoreStatus | None = None,
) -> list[dict[str, Any]]:
    """alerts.log에서 최근 이벤트를 파싱해 반환(최신순). 자유서술 필드는 마스킹.

    append-only 로그를 deque(maxlen)로 스트리밍해 메모리를 제한한다(장기운영 로그 비대화 대비).
    필터는 AND 조건이며, since/until은 ISO8601/Z 타임스탬프를 파싱해 비교한다.
    """
    limit = max(1, min(int(limit), EVENTS_MAX_LIMIT))
    offset = max(0, int(offset))
    # Durable SQLite repository is the preferred history source. Keep the log
    # parser fallback for backward compatibility and for deployments upgrading
    # before the repository exists. Re-mask defensively even though AlertManager
    # persists masked payloads.
    if event_repository is not None:
        try:
            items = event_repository.list(
                limit=limit,
                offset=offset,
                event_type=event_type,
                camera_id=camera_id,
                alert_level=alert_level,
                since=since,
                until=until,
            )
            if durable_store_status is not None:
                durable_store_status.record_success(operation="query:list")
            return [_mask_event(item) for item in items]
        except Exception as error:
            # Do not make API history unavailable if a new repository is damaged;
            # the rotating alert log remains an operational fallback.
            if durable_store_status is not None:
                durable_store_status.record_failure(error, operation="query:list")
            else:
                logger.warning(
                    "durable event store query failed; using legacy alert log fallback",
                    extra={"operation": "list_events", "error_type": type(error).__name__},
                )

    path = alerts_log or _ALERTS_LOG
    since_dt = _parse_iso_timestamp(since)
    until_dt = _parse_iso_timestamp(until)
    if not path.exists():
        return []
    buf: collections.deque = collections.deque(maxlen=EVENTS_MAX_LIMIT)
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = parse_alert_log_line(line)
            except Exception:
                continue
            if ev is None:
                continue
            buf.append(_mask_event(ev))
    events = list(buf)
    events.reverse()  # 최신순
    canonical = []
    seen_event_ids: set[str] = set()
    for ev in events:
        event_id = ev.get("event_id")
        if isinstance(event_id, str) and event_id:
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)
        if _event_matches(ev, event_type, camera_id, alert_level, since_dt, until_dt):
            canonical.append(ev)
    return canonical[offset : offset + limit]


def get_event(
    event_id: str,
    *,
    alerts_log: Path | None = None,
    event_repository: Any | None = None,
    durable_store_status: DurableEventStoreStatus | None = None,
) -> dict[str, Any] | None:
    """Return one event by exact ID, preferring durable state over legacy logs."""
    if not isinstance(event_id, str) or not event_id:
        return None

    durable_error = False
    if event_repository is not None:
        try:
            event = event_repository.get(event_id)
        except Exception as error:
            durable_error = True
            if durable_store_status is not None:
                durable_store_status.record_failure(error, operation="query:get")
            logger.warning("durable event lookup failed", exc_info=True)
        else:
            if durable_store_status is not None:
                durable_store_status.record_success(operation="query:get")
            if event is not None:
                return _mask_event(event)

    path = alerts_log or _ALERTS_LOG
    if not path.exists():
        if durable_error:
            raise EventLookupUnavailable("durable event repository unavailable")
        return None
    try:
        with open(path, encoding="utf-8") as file:
            # Keep the final matching lifecycle record; logs are append-only.
            matching_event: dict[str, Any] | None = None
            for line in file:
                try:
                    event = parse_alert_log_line(line.strip())
                except Exception:
                    continue
                if isinstance(event, dict) and event.get("event_id") == event_id:
                    matching_event = event
    except OSError as error:
        raise EventLookupUnavailable("legacy event log unavailable") from error
    return _mask_event(matching_event) if matching_event is not None else None


def _mask_event(ev: dict[str, Any]) -> dict[str, Any]:
    masked = dict(ev)
    for key in _EVENT_TEXT_FIELDS:
        val = masked.get(key)
        if isinstance(val, str) and val:
            masked[key] = mask_text(val)
    evs = masked.get("vlm_detected_events")
    if isinstance(evs, list):
        masked["vlm_detected_events"] = [mask_text(e) if isinstance(e, str) else e for e in evs]
    return masked


async def _tail_alerts_log(
    is_disconnected: Callable[[], Awaitable[bool]],
    alerts_log: Path | None = None,
    poll_interval: float = 1.0,
    heartbeat_every: int = 15,
    start_pos: int | None = None,
) -> AsyncIterator[dict[str, Any] | None]:
    """alerts.log의 신규 이벤트를 dict로 yield(공통 tail 로직).

    SSE/WS 양쪽에서 재사용한다. None은 heartbeat 신호. 파일 트렁케이트/로테이션 시
    위치를 재설정하고, None을 주기적으로 yield해 연결을 유지한다.
    list_events와 동일한 파싱·마스킹(_mask_event)을 재사용한다.
    """
    path = alerts_log or _ALERTS_LOG
    # SSE replay와 tail 사이의 write race 방지: stream_events가 연결 시작 시점의
    # byte offset을 넘기면 그 위치부터 tail한다. replay와 tail에 겹침이 생겨도
    # event_id 기반 클라이언트 upsert가 처리하며, 유실보다 중복이 안전하다.
    pos = start_pos if start_pos is not None else (path.stat().st_size if path.exists() else 0)
    pending = ""
    beats = 0
    while True:
        if await is_disconnected():
            return
        try:
            if path.exists():
                size = path.stat().st_size
                if size < pos:  # 로테이션/트렁케이트 → 처음부터 재구독
                    pos, pending = 0, ""
                if size > pos:
                    with open(path, encoding="utf-8") as f:
                        f.seek(pos)
                        data = f.read()
                        pos = f.tell()
                    pending += data
                    parts = pending.split("\n")
                    pending = parts.pop()  # 마지막 조각은 미완성 라인일 수 있음
                    for raw in parts:
                        line = raw.strip()
                        if not line:
                            continue
                        try:
                            ev = parse_alert_log_line(line)
                        except Exception:
                            continue
                        if ev is None:
                            continue
                        yield _mask_event(ev)
        except Exception:
            pass
        beats += 1
        if beats % max(1, heartbeat_every) == 0:
            yield None  # heartbeat marker
        await asyncio.sleep(poll_interval)


def _replay_events_after(
    last_event_id: str,
    alerts_log: Path | None = None,
) -> list[dict[str, Any]] | None:
    """last_event_id 이후의 로그 이벤트를 시간순으로 반환한다.

    반환값이 None이면 cursor가 현재 로그에 없거나 로그가 없어 REST reconciliation이
    필요하다는 뜻이다. 빈 list는 cursor가 로그 끝이라는 정상 케이스다.

    event_id는 기존 API/로그에서 이미 쓰는 공개 식별자다. lifecycle update가 같은 id를
    재사용할 수 있으므로 이 단계는 at-least-once 전달이며, frontend는 id 기반 upsert로
    최신 payload를 보존한다. 엄격한 per-transition sequence는 durable event storage 단계에서
    별도 stream_id로 보강한다.
    """
    path = alerts_log or _ALERTS_LOG
    if not last_event_id or not path.exists():
        return None
    try:
        events: list[dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    ev = parse_alert_log_line(line.strip())
                except Exception:
                    continue
                if ev is not None:
                    events.append(_mask_event(ev))
    except Exception:
        return None

    # lifecycle event가 동일 id를 재사용할 수 있으므로 가장 최근 cursor occurrence 뒤부터
    # replay한다. 이미 받은 provisional update를 다시 보내지 않고 이후 state를 보낸다.
    cursor_index = -1
    for index, ev in enumerate(events):
        if ev.get("event_id") == last_event_id:
            cursor_index = index
    if cursor_index < 0:
        return None
    return events[cursor_index + 1 :]


def _sse_alert_frame(ev: dict[str, Any], stream_id: int | None = None) -> str:
    """마스킹된 event dict를 SSE alert frame으로 만든다."""
    payload = json.dumps(ev, ensure_ascii=False)
    event_id = ev.get("event_id")
    cursor = str(stream_id) if stream_id is not None else event_id
    # event_id 없는 legacy log line도 호환되게 alert payload만 보낸다.
    prefix = f"id: {cursor}\n" if isinstance(cursor, str) and cursor else ""
    return f"{prefix}event: alert\ndata: {payload}\n\n"


def prepare_durable_event_cursor(
    event_repository: Any,
    last_event_id: str | None,
    durable_store_status: DurableEventStoreStatus | None = None,
) -> tuple[int, str | None]:
    """Capture one durable replay cursor and describe any unavailable client cursor."""
    try:
        latest = int(event_repository.latest_stream_id())
        first = int(event_repository.first_stream_id())
    except Exception as error:
        if durable_store_status is not None:
            durable_store_status.record_failure(error, operation="query:stream")
        raise
    if durable_store_status is not None:
        durable_store_status.record_success(operation="query:stream")
    cursor = latest
    if not last_event_id:
        return cursor, None
    try:
        requested = int(last_event_id)
    except ValueError:
        requested = -1
    if requested < 0 or requested > latest:
        return cursor, "cursor-unavailable"
    if first and requested < first - 1:
        return cursor, "cursor-expired"
    return requested, None


async def _stream_durable_events(
    is_disconnected: Callable[[], Awaitable[bool]],
    event_repository: Any,
    *,
    poll_interval: float,
    heartbeat_every: int,
    last_event_id: str | None,
    durable_store_status: DurableEventStoreStatus | None,
) -> AsyncIterator[str]:
    """Stream SQLite lifecycle revisions with monotonic durable SSE cursors."""
    try:
        cursor, reconciliation_reason = prepare_durable_event_cursor(
            event_repository, last_event_id, durable_store_status
        )
    except Exception:
        yield ": connected\n\n"
        yield "event: reconciliation-required\ndata: {\"reason\":\"durable-store-unavailable\"}\n\n"
        return

    yield ": connected\n\n"
    if reconciliation_reason:
        yield f"event: reconciliation-required\ndata: {{\"reason\":\"{reconciliation_reason}\"}}\n\n"
    beats = 0
    while True:
        if await is_disconnected():
            return
        try:
            records = event_repository.stream_records_after(cursor)
        except Exception as error:
            if durable_store_status is not None:
                durable_store_status.record_failure(error, operation="query:stream")
            yield "event: reconciliation-required\ndata: {\"reason\":\"durable-store-unavailable\"}\n\n"
            return
        if durable_store_status is not None:
            durable_store_status.record_success(operation="query:stream")
        for stream_id, event in records:
            cursor = stream_id
            yield _sse_alert_frame(_mask_event(event), stream_id)
        beats += 1
        if beats % max(1, heartbeat_every) == 0:
            yield ": keepalive\n\n"
        await asyncio.sleep(poll_interval)


async def stream_events(
    is_disconnected: Callable[[], Awaitable[bool]],
    alerts_log: Path | None = None,
    poll_interval: float = 1.0,
    heartbeat_every: int = 15,
    last_event_id: str | None = None,
    event_repository: Any | None = None,
    durable_store_status: DurableEventStoreStatus | None = None,
) -> AsyncIterator[str]:
    """alerts.log의 신규 이벤트를 SSE 프레임 문자열로 yield한다.

    cursor가 없으면 기존처럼 파일 끝 이후의 신규 라인만 push한다. Last-Event-ID가 있으면
    연결 시작 시점의 log offset을 먼저 고정하고 cursor 이후 이벤트를 replay한 뒤 tail한다.
    cursor가 rotation/retention으로 사라진 경우에는 reconciliation-required 이벤트를 보내
    클라이언트가 REST 목록을 다시 병합하게 한다.
    파일 트렁케이트/로테이션 시 위치를 재설정하고, heartbeat 주석으로 연결을 유지한다.
    list_events와 동일한 파싱·마스킹(_mask_event)을 재사용한다.
    """
    if event_repository is not None:
        async for frame in _stream_durable_events(
            is_disconnected,
            event_repository,
            poll_interval=poll_interval,
            heartbeat_every=heartbeat_every,
            last_event_id=last_event_id,
            durable_store_status=durable_store_status,
        ):
            yield frame
        return

    path = alerts_log or _ALERTS_LOG
    # replay/tail 사이 write race 방지를 위한 연결 시작 snapshot. 경합으로 중복은 가능하지만
    # 유실은 없고, frontend의 event_id upsert가 중복을 제거한다.
    start_pos = path.stat().st_size if path.exists() else 0

    # 연결 즉시 첫 바이트를 흘려보내 헤더 flush + 클라 onopen을 빠르게 트리거(첫 이벤트/heartbeat
    # 까지 기다리지 않게 — "실시간 수신 중" 표시가 즉시 뜬다).
    yield ": connected\n\n"
    if last_event_id:
        replay = _replay_events_after(last_event_id, path)
        if replay is None:
            yield "event: reconciliation-required\ndata: {\"reason\":\"cursor-unavailable\"}\n\n"
        else:
            for ev in replay:
                yield _sse_alert_frame(ev)

    async for tail_event in _tail_alerts_log(
        is_disconnected,
        path,
        poll_interval,
        heartbeat_every,
        start_pos=start_pos,
    ):
        if tail_event is None:
            yield ": keepalive\n\n"
        else:
            yield _sse_alert_frame(tail_event)


async def stream_events_ws(
    is_disconnected: Callable[[], Awaitable[bool]],
    alerts_log: Path | None = None,
    poll_interval: float = 1.0,
    heartbeat_every: int = 15,
    event_repository: Any | None = None,
    cursor: int | None = None,
    reconciliation_reason: str | None = None,
    durable_store_status: DurableEventStoreStatus | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """alerts.log의 신규 이벤트를 dict로 yield(WebSocket용 — SSE 프레임 아님).

    초기 이벤트는 호출자가 전달하며, cursor는 그 snapshot 전에 고정되어야 한다.
    heartbeat는 None이 아닌 빈 dict로 전달해 클라이언트가 type 필드로 구분 가능하게 한다.
    """
    if event_repository is not None:
        try:
            if cursor is None:
                cursor, reconciliation_reason = prepare_durable_event_cursor(
                    event_repository, None, durable_store_status
                )
        except Exception:
            yield {"type": "reconciliation-required", "reason": "durable-store-unavailable"}
            return
        if reconciliation_reason:
            yield {"type": "reconciliation-required", "reason": reconciliation_reason}
        beats = 0
        while True:
            if await is_disconnected():
                return
            try:
                records = event_repository.stream_records_after(cursor)
            except Exception as error:
                if durable_store_status is not None:
                    durable_store_status.record_failure(error, operation="query:stream")
                yield {"type": "reconciliation-required", "reason": "durable-store-unavailable"}
                return
            if durable_store_status is not None:
                durable_store_status.record_success(operation="query:stream")
            for stream_id, event in records:
                cursor = stream_id
                yield {"type": "event", "data": _mask_event(event), "stream_id": stream_id}
            beats += 1
            if beats % max(1, heartbeat_every) == 0:
                yield {"type": "heartbeat"}
            await asyncio.sleep(poll_interval)
        return

    async for ev in _tail_alerts_log(is_disconnected, alerts_log, poll_interval, heartbeat_every):
        if ev is None:
            yield {"type": "heartbeat"}
        else:
            yield {"type": "event", "data": ev}


def list_cameras(config: Any) -> list[dict[str, Any]]:
    """카메라 목록 — stream_url 자격증명 마스킹."""
    out = []
    for cam in config.load_cameras():
        out.append({
            "id": cam.get("id"),
            "name": cam.get("name"),
            "location": cam.get("location"),
            "fps": cam.get("fps"),
            "resolution": cam.get("resolution"),
            "enabled": bool(cam.get("enabled", False)),
            "detection_type": cam.get("detection_type"),
            "stream_url": mask_url(cam.get("stream_url", "") or ""),  # 자격증명 마스킹
        })
    return out


def list_models(profiles_path: Path | None = None) -> list[dict[str, Any]]:
    """등록 모델 목록(model_profiles.yaml) — ONNX를 로드하지 않고 메타만 읽음(가벼움)."""
    cfg = _load_yaml(profiles_path or _MODEL_PROFILES)
    out = []
    for name, m in (cfg.get("models") or {}).items():
        if not isinstance(m, dict):
            continue
        out.append({
            "name": name,
            "type": m.get("type"),
            "source": m.get("source"),
            "enabled": bool(m.get("enabled", False)),
            "class_profile": m.get("class_profile"),
            "classes": m.get("classes"),
            "input_size": m.get("input_size"),
            "postprocess_mode": m.get("postprocess_mode"),
            "has_class_map": bool(m.get("class_map")),
            "description": m.get("description"),
        })
    return out


def list_policies(policies_path: Path | None = None) -> dict[str, Any]:
    """이벤트 정책 요약(event_policies.yaml) — 프로파일별 활성 이벤트와 핵심 메타."""
    cfg = _load_yaml(policies_path or _EVENT_POLICIES)
    profiles_out: dict[str, Any] = {}
    for pname, pbody in (cfg.get("profiles") or {}).items():
        policies = (pbody or {}).get("policies", {}) if isinstance(pbody, dict) else {}
        events = []
        for ename, e in policies.items():
            if not isinstance(e, dict):
                continue
            events.append({
                "name": ename,
                "enabled": bool(e.get("enabled", False)),
                "classes": (e.get("trigger") or {}).get("classes", []),
                "rule": (e.get("rule") or {}).get("type"),
                "alert_level": e.get("alert_level"),
                "vlm": e.get("vlm"),
                "cooldown_sec": e.get("cooldown_sec"),
                "temporal": bool((e.get("rule") or {}).get("temporal")),
            })
        profiles_out[pname] = events
    return {"profiles": profiles_out}


def get_event_catalog(
    policies_path: Path | None = None,
    camera_profiles_path: Path | None = None,
) -> dict[str, Any]:
    """Return the canonical event catalog and runtime availability metadata."""
    path = policies_path or _EVENT_POLICIES
    cfg = _load_yaml(path)
    pol = list_policies(path)
    profiles_out: dict[str, list[dict[str, Any]]] = pol.get("profiles", {})

    seen: dict[str, dict[str, Any]] = {}
    for domain_name, events in profiles_out.items():
        for ev in events:
            if not isinstance(ev, dict):
                continue
            name = ev.get("name")
            if not name:
                continue
            if name not in seen:
                seen[name] = {
                    "name": name,
                    "alert_level": ev.get("alert_level"),
                    "vlm": ev.get("vlm"),
                    "cooldown_sec": ev.get("cooldown_sec"),
                    "defined_in": [domain_name],
                }
            else:
                seen[name]["defined_in"].append(domain_name)

    configured = cfg.get("catalog")
    catalog = []
    names = set(seen)
    if isinstance(configured, dict):
        names.update(str(name) for name in configured)
    for name in sorted(names):
        item = seen.get(name, {})
        metadata = configured.get(name, {}) if isinstance(configured, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        status = str(metadata.get("status", "active"))
        runtime = str(metadata.get("runtime", "detection"))
        canonical_profile = metadata.get("canonical_profile")
        canonical_body = (cfg.get("profiles") or {}).get(canonical_profile, {})
        canonical_policy = (
            (canonical_body.get("policies") or {}).get(name, {})
            if isinstance(canonical_body, dict)
            else {}
        )
        if not isinstance(canonical_policy, dict):
            canonical_policy = {}
        trigger = canonical_policy.get("trigger", {}) or {}
        rule = canonical_policy.get("rule", {}) or {}
        tracking = None
        if runtime == "tracking":
            tracking = {
                "classes": list(trigger.get("classes", []) or []),
                "min_confidence": float(trigger.get("min_confidence", 0.0)),
                "dwell_time_sec": rule.get("dwell_time_sec"),
                "grace_sec": rule.get("grace_sec"),
                "min_displacement_px": rule.get("min_displacement_px"),
            }
        release_qualified, qualification_reason = release_qualification(name, status)
        configured_selectable = bool(metadata.get("selectable", status in {"active", "experimental"}))
        selectable = release_qualified and configured_selectable
        blocked_reason = (
            qualification_reason
            if not release_qualified
            else (str(metadata["reason"]) if not configured_selectable and metadata.get("reason") else None)
        )
        catalog.append({
            "name": name,
            "label": metadata.get("label", name),
            "category": metadata.get("category", "other"),
            "status": status,
            "runtime": runtime,
            "release_qualified": release_qualified,
            "selectable": selectable,
            "blocked_reason": blocked_reason,
            "reason": blocked_reason or metadata.get("reason"),
            "requires_zone": metadata.get("requires_zone"),
            "alert_level": canonical_policy.get("alert_level", item.get("alert_level")),
            "vlm": canonical_policy.get("vlm", item.get("vlm")),
            "cooldown_sec": canonical_policy.get(
                "cooldown_sec", item.get("cooldown_sec")
            ),
            "tracking": tracking,
            "domains": sorted(item.get("defined_in", [])),
        })

    profile_cfg = _load_yaml(camera_profiles_path or _CAMERA_PROFILES)
    profile_presets: dict[str, dict[str, Any]] = {}
    for domain, body in (profile_cfg.get("domains") or {}).items():
        if isinstance(body, dict):
            profile_presets[str(domain)] = {
                "label": body.get("label", domain),
                "default_events": [str(item) for item in (body.get("default_events") or [])],
            }

    return {
        "catalog": catalog,
        "count": len(catalog),
        "profiles": profile_presets,
    }


def event_zone_warnings(
    enabled_events: list[str],
    zones: list[Any],
    policies_path: Path | None = None,
) -> list[str]:
    """Return non-blocking warnings for selected events missing required zones."""
    zone_types: dict[str, str] = {}
    for zone in zones:
        if isinstance(zone, dict):
            zone_id = zone.get("id")
            zone_type = zone.get("type", "polygon")
        else:
            zone_id = getattr(zone, "id", None)
            zone_type = getattr(zone, "type", "polygon")
        if zone_id:
            zone_types[str(zone_id)] = str(zone_type)

    by_name = {
        item["name"]: item
        for item in get_event_catalog(policies_path).get("catalog", [])
        if isinstance(item, dict) and item.get("name")
    }
    warnings: list[str] = []
    for event_name in enabled_events:
        required = by_name.get(event_name, {}).get("requires_zone")
        if not required:
            continue
        expected_type = "line" if required == "line" else "polygon"
        actual_type = zone_types.get(str(required))
        if actual_type is None:
            warnings.append(f"{event_name}: '{required}' zone is required")
        elif actual_type != expected_type:
            warnings.append(
                f"{event_name}: '{required}' zone must be {expected_type}"
            )
    return warnings


def validate_event_selection(
    domain: str,
    enabled_events: list[str],
    policies_path: Path | None = None,
) -> list[str]:
    """Validate a camera domain and preserve an explicit, ordered event selection."""
    profiles = list_policies(policies_path).get("profiles", {})
    if domain not in profiles:
        raise ValueError(f"알 수 없는 도메인입니다: {domain} (허용: {', '.join(profiles)})")

    catalog = get_event_catalog(policies_path).get("catalog", [])
    selectable = {
        item["name"]
        for item in catalog
        if isinstance(item, dict) and item.get("selectable") is True
    }
    known = {item["name"] for item in catalog if isinstance(item, dict)}
    requested = list(dict.fromkeys(enabled_events or []))
    unknown = [event for event in requested if event not in known]
    unavailable = [event for event in requested if event in known and event not in selectable]
    if unknown:
        raise ValueError(f"알 수 없는 이벤트가 포함되어 있습니다: {unknown}")
    if unavailable:
        raise ValueError(f"아직 사용할 수 없는 이벤트가 포함되어 있습니다: {unavailable}")
    return requested


MAX_PROMPTS = 100
MAX_PROMPT_LEN = 64


def _write_yaml(path: Path, data: Any) -> None:
    """설정 쓰기. ruamel round-trip 덤프로 CommentedMap의 주석을 보존한다(plain dict면 주석 없이 정상 덤프)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        _yaml_rt.dump(data, f)


def _clean_prompts(classes: list[str]) -> list[str]:
    """프롬프트 정제 — 공백 strip, 빈/과길이 제거, 중복 제거(순서 보존), 개수 상한."""
    seen: set[str] = set()
    out: list[str] = []
    for c in classes or []:
        if not isinstance(c, str):
            continue
        s = c.strip()
        if not s or len(s) > MAX_PROMPT_LEN:
            continue
        if any(ord(ch) < 32 for ch in s):  # 제어문자 거부
            continue
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out[:MAX_PROMPTS]


def set_model_prompts(
    classes: list[str],
    model_name: str = "open_vocab_yolo",
    enabled: bool | None = None,
    profiles_path: Path | None = None,
) -> dict[str, Any]:
    """open_vocab 모델의 탐지 프롬프트(classes)를 설정한다(학습 없이 즉시 탐지 대상 변경).

    model_profiles.yaml을 안전 갱신한다. open_vocab 타입만 허용. 반환은 갱신된 모델 요약.
    KeyError(모델 없음)·ValueError(타입 불가/유효 프롬프트 없음)를 발생시킨다(라우트가 4xx로 변환).
    """
    path = profiles_path or _MODEL_PROFILES
    cfg = _load_yaml_rt(path)
    models = cfg.get("models")
    if not isinstance(models, dict) or model_name not in models:
        raise KeyError(f"모델을 찾을 수 없습니다: {model_name}")
    m = models[model_name]
    if not isinstance(m, dict) or m.get("type") != "open_vocab":
        raise ValueError("open_vocab 타입 모델만 프롬프트를 설정할 수 있습니다.")
    cleaned = _clean_prompts(classes)
    if not cleaned:
        raise ValueError("유효한 프롬프트가 없습니다(빈 문자열/64자 초과만 입력됨).")
    m["classes"] = cleaned
    if enabled is not None:
        m["enabled"] = bool(enabled)
    _write_yaml(path, cfg)
    return {"name": model_name, "classes": cleaned, "enabled": bool(m.get("enabled", False))}


def set_model_enabled(
    model_name: str, enabled: bool, profiles_path: Path | None = None
) -> dict[str, Any]:
    """모델 활성/비활성 토글(model_profiles.yaml 갱신). 반환은 갱신된 상태."""
    path = profiles_path or _MODEL_PROFILES
    cfg = _load_yaml_rt(path)
    models = cfg.get("models")
    if not isinstance(models, dict) or model_name not in models:
        raise KeyError(f"모델을 찾을 수 없습니다: {model_name}")
    models[model_name]["enabled"] = bool(enabled)
    _write_yaml(path, cfg)
    return {"name": model_name, "enabled": bool(enabled)}


def delete_uploaded_model(
    name: str,
    profiles_path: Path | None = None,
    upload_base_dir: Path | None = None,
) -> dict[str, Any]:
    """업로드한 모델을 삭제(model_profiles에서 제거 + 격리 디렉토리 정리). 기본 모델은 보호.

    안전: source=='uploaded'만 허용(빌트인/사전학습 보호) + 디렉토리 정리는 models/uploaded 하위로 한정.
    """
    path = profiles_path or _MODEL_PROFILES
    cfg = _load_yaml_rt(path)
    models = cfg.get("models")
    if not isinstance(models, dict) or name not in models:
        raise KeyError(f"모델을 찾을 수 없습니다: {name}")
    prof = models[name] if isinstance(models[name], dict) else {}
    if prof.get("source") != "uploaded":
        raise ValueError("업로드한 모델만 삭제할 수 있습니다(기본 모델은 보호됩니다).")

    base = upload_base_dir or (_ROOT / "models" / "uploaded")
    removed_dir = False
    model_path = prof.get("model_path", "")
    if model_path:
        target = Path(model_path).resolve().parent  # models/uploaded/<id>
        try:
            if str(target).startswith(str(Path(base).resolve()) + os.sep) and target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                removed_dir = True
        except Exception:
            pass

    del models[name]
    _write_yaml(path, cfg)
    return {"name": name, "deleted": True, "removed_dir": removed_dir}


def handle_model_upload(
    *,
    file_bytes: bytes | None = None,
    file_path: Path | None = None,
    original_filename: str,
    model_name: str,
    classes: list[str],
    input_size: list[int],
) -> dict[str, Any]:
    """업로드된 ONNX를 기존 하드닝 검증기(샌드박스)로 처리하고 disabled로 등록한다.

    보안 핵심 경로는 src.models.upload_validator.upload_model을 그대로 재사용한다(external-data
    차단 → load → shape → smoke 추론을 단일 rlimit sandbox에서). 여기서는 API 어댑터일 뿐이며
    서버 경로(model_path) 등 내부 정보는 응답에서 제외한다. onnx 의존성은 지연 import."""
    from src.models import upload_validator

    metadata = {"model_name": model_name, "classes": classes, "input_size": input_size}
    if (file_bytes is None) == (file_path is None):
        return {"success": False, "error": "업로드 입력은 하나만 제공해야 합니다."}
    if file_path is not None:
        result = upload_validator.upload_model_file(file_path, original_filename, metadata=metadata)
    else:
        result = upload_validator.upload_model(file_bytes or b"", original_filename, metadata=metadata)
    if not result.success:
        return {"success": False, "error": result.error}
    meta = result.metadata or {}
    return {
        "success": True,
        "model_id": result.model_id,
        "profile_name": result.profile_name,
        "model_name": meta.get("model_name"),
        "classes": meta.get("classes", []),
        "class_profile": meta.get("class_profile"),
        "enabled": False,  # 항상 비활성으로 등록(운영자가 검토 후 활성화)
    }


def canonical_json_bytes(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _audit_key_paths(path: Path) -> tuple[Path, Path]:
    private = path.with_name(f"{path.name}.ed25519")
    return private, private.with_name(f"{private.name}.pub")


def _fsync_parent(path: Path) -> None:
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _write_audit_identity_file(path: Path, value: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
        _fsync_parent(temporary)
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        try:
            written = 0
            while written < len(value):
                count = os.write(fd, value[written:])
                if count <= 0:
                    raise OSError("short audit identity write")
                written += count
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(temporary, path)
        os.chmod(path, mode)
        _fsync_parent(path)
    finally:
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
            _fsync_parent(temporary)


def _audit_history_exists(path: Path) -> bool:
    return any(
        candidate.exists() or candidate.is_symlink()
        for candidate in (
            path,
            *(path.with_name(f"{path.name}.{index}") for index in range(1, _AUDIT_MAX_SEGMENTS + 1)),
        )
    )


def _load_or_create_audit_key(path: Path) -> Ed25519PrivateKey:
    private_path, public_path = _audit_key_paths(path)
    private_exists = private_path.exists() or private_path.is_symlink()
    public_exists = public_path.exists() or public_path.is_symlink()
    if private_exists != public_exists:
        raise RuntimeError("audit signing identity is incomplete; operator recovery is required")
    if private_exists:
        if private_path.is_symlink() or public_path.is_symlink() or not private_path.is_file() or not public_path.is_file():
            raise RuntimeError("audit signing identity paths are unsafe")
        try:
            key = Ed25519PrivateKey.from_private_bytes(private_path.read_bytes())
            public_bytes = base64.b64decode(public_path.read_text(encoding="ascii").strip(), validate=True)
            expected_public = key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            raise RuntimeError("audit signing identity is unreadable") from exc
        if public_bytes != expected_public:
            raise RuntimeError("audit signing public key does not match its private key")
        os.chmod(private_path, 0o600)
        os.chmod(public_path, 0o644)
        return key
    if _audit_history_exists(path):
        raise RuntimeError("audit history exists without its signing identity")
    key = Ed25519PrivateKey.generate()
    private_bytes = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    public_bytes = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    _write_audit_identity_file(private_path, private_bytes, 0o600)
    _write_audit_identity_file(
        public_path,
        base64.b64encode(public_bytes) + b"\n",
        0o644,
    )
    return key


def _audit_lock(path: Path):
    lock_path = path.with_name(f"{path.name}.lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def audit_public_key(audit_log: Path | None = None) -> str:
    """Return the base64 Raw Ed25519 public key for an audit trail."""
    path = audit_log or _AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _audit_lock(path)
    try:
        key = _load_or_create_audit_key(path)
        return base64.b64encode(
            key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        ).decode("ascii")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def sign_audit_data(data: bytes, audit_log: Path | None = None) -> str:
    """Sign externally verifiable data with the local audit signing identity."""
    path = audit_log or _AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _audit_lock(path)
    try:
        return base64.b64encode(_load_or_create_audit_key(path).sign(data)).decode("ascii")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _last_audit_hash(path: Path) -> str | None:
    for candidate in [path, *(path.with_name(f"{path.name}.{index}") for index in range(1, _AUDIT_MAX_SEGMENTS + 1))]:
        if not candidate.exists():
            continue
        for line in reversed(candidate.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entry_hash = entry.get("entry_hash") if isinstance(entry, dict) else None
            if isinstance(entry_hash, str):
                return entry_hash
    return None


def _repair_incomplete_audit_tail(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        return
    with path.open("r+b") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(0)
        data = handle.read()
        if data.endswith(b"\n"):
            return
        boundary = data.rfind(b"\n") + 1
        handle.truncate(boundary)
        handle.flush()
        os.fsync(handle.fileno())
    logger.error("Truncated incomplete audit tail after interrupted write: %s bytes removed", size - boundary)


def _rotate_audit_if_needed(path: Path, incoming_bytes: int) -> None:
    if not path.is_file() or path.stat().st_size + incoming_bytes <= _AUDIT_MAX_BYTES:
        return
    oldest = path.with_name(f"{path.name}.{_AUDIT_MAX_SEGMENTS}")
    oldest.unlink(missing_ok=True)
    for index in range(_AUDIT_MAX_SEGMENTS - 1, 0, -1):
        source = path.with_name(f"{path.name}.{index}")
        if source.exists():
            os.replace(source, path.with_name(f"{path.name}.{index + 1}"))
    os.replace(path, path.with_name(f"{path.name}.1"))
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _audit_segments_oldest_first(path: Path) -> list[Path]:
    segments = [
        path.with_name(f"{path.name}.{index}")
        for index in range(_AUDIT_MAX_SEGMENTS, 0, -1)
        if path.with_name(f"{path.name}.{index}").is_file()
    ]
    if path.is_file():
        segments.append(path)
    return segments


def write_audit(event: str, audit_log: Path | None = None, **fields: Any) -> bool:
    """Durably append a signed, hash-chained audit entry without blocking mutations on failure."""
    path = audit_log or _AUDIT_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_fd = _audit_lock(path)
        try:
            _repair_incomplete_audit_tail(path)
            entry = _mask_audit({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": event,
                "source": "api",
                "actor_type": "api-key",
                "actor": "api-key",
                "outcome": "success",
                "status_code": 200,
                **fields,
            })
            entry["previous_hash"] = _last_audit_hash(path)
            entry["entry_hash"] = hashlib.sha256(canonical_json_bytes(entry)).hexdigest()
            entry["signature"] = base64.b64encode(
                _load_or_create_audit_key(path).sign(bytes.fromhex(entry["entry_hash"]))
            ).decode("ascii")
            data = canonical_json_bytes(entry) + b"\n"
            _rotate_audit_if_needed(path, len(data))
            created = not path.exists()
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                written = 0
                while written < len(data):
                    count = os.write(fd, data[written:])
                    if count <= 0:
                        raise OSError("short audit log write")
                    written += count
                os.fsync(fd)
            finally:
                os.close(fd)
            if created:
                directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        return True
    except Exception as exc:
        # Best-effort remains intentional for life-safety/control availability, but silent
        # loss is unacceptable: surface a structured operational error to process logs.
        logger.error("Audit write failed for %s: %s", event, type(exc).__name__)
        return False


def _mask_audit(entry: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact secret-shaped keys and values before storage or exposure."""
    def redact(value: Any, key: str | None = None) -> Any:
        if key and _AUDIT_SECRET_KEY.search(key):
            return "[REDACTED]"
        if isinstance(value, dict):
            return {str(item_key): redact(item_value, str(item_key)) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, tuple):
            return [redact(item) for item in value]
        if isinstance(value, str):
            masked = mask_text(value)
            return "[REDACTED]" if _AUDIT_SECRET_VALUE.search(masked) else masked
        return value

    return redact(entry)


def list_audit_window(
    limit: int = 200, audit_log: Path | None = None
) -> tuple[list[dict[str, Any]], str | None]:
    """Return newest-first audit entries and the retained chain's preceding hash."""
    path = audit_log or _AUDIT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _audit_lock(path)
    try:
        segments = _audit_segments_oldest_first(path)
        if not segments:
            return [], None
        safe_limit = max(1, min(int(limit), 1000))
        retained: collections.deque[dict[str, Any]] = collections.deque(maxlen=safe_limit)
        for segment in segments:
            with segment.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    if isinstance(entry, dict):
                        retained.append(_mask_audit(entry))
        window = list(reversed(retained))
        anchor = window[-1].get("previous_hash") if window else None
        return window, anchor if isinstance(anchor, str) else None
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def list_audit(limit: int = 200, audit_log: Path | None = None) -> list[dict[str, Any]]:
    """감사 로그를 최신순으로 반환(마스킹). JSONL 파싱 실패 줄은 skip."""
    return list_audit_window(limit, audit_log)[0]


def _load_capture_fernet():
    """KSI_CCTV_CAPTURE_KEY에서 Fernet 로드(.jpg.enc 복호화용). 미설정/실패 시 None."""
    key = os.environ.get("KSI_CCTV_CAPTURE_KEY", "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet

        return Fernet(key.encode())
    except Exception:
        return None


def list_captures(captures_dir: Path | None = None, limit_per_camera: int = 200) -> dict[str, Any]:
    """data/captures를 날짜→카메라→이미지 트리로 반환(최신순). 이미지는 얼굴 블러가 저장 시 적용됨.

    구조: data/captures/{YYYYMMDD}/{camera_id}/{name}.jpg[.enc]. 파일명만 노출(서버 절대경로 미노출).
    """
    base = captures_dir or _CAPTURES_DIR
    if not base.exists():
        return {"dates": []}
    dates: list[dict[str, Any]] = []
    for ddir in sorted(
        (d for d in base.iterdir() if d.is_dir() and _DATE_RE.match(d.name)),
        key=lambda p: p.name,
        reverse=True,
    ):
        cams: list[dict[str, Any]] = []
        for cdir in sorted((c for c in ddir.iterdir() if c.is_dir()), key=lambda p: p.name):
            images: list[dict[str, Any]] = []
            for f in sorted(cdir.iterdir(), key=lambda p: p.name, reverse=True):
                if not f.is_file():
                    continue
                if f.name.endswith(".jpg.enc"):
                    images.append({"name": f.name, "encrypted": True})
                elif f.suffix.lower() == ".jpg":
                    images.append({"name": f.name, "encrypted": False})
                if len(images) >= limit_per_camera:
                    break
            if images:
                cams.append({"camera_id": cdir.name, "count": len(images), "images": images})
        if cams:
            dates.append({"date": ddir.name, "cameras": cams})
    return {"dates": dates}


def read_capture_image(
    date: str, camera: str, name: str, captures_dir: Path | None = None
) -> bytes | None:
    """검증된 경로의 캡처 이미지 bytes 반환(.enc는 복호화). 경로 위반/부재/복호화불가 시 None.

    경로 주입 방지: date=YYYYMMDD, camera/name은 영숫자·._-만(슬래시·.. 불허) + resolve 후 base 하위 확인.
    """
    base = captures_dir or _CAPTURES_DIR
    if not (_DATE_RE.match(date) and _SAFE_SEG.match(camera) and _SAFE_SEG.match(name)):
        return None
    if camera in (".", "..") or name in (".", ".."):  # 정규식이 점만 있는 세그먼트는 허용하므로 명시 차단
        return None
    if not (name.endswith(".jpg") or name.endswith(".jpg.enc")):
        return None
    target = base / date / camera / name
    try:
        rp = target.resolve()
        if not str(rp).startswith(str(base.resolve()) + os.sep):
            return None  # traversal 방어(이중)
        if not rp.is_file():
            return None
        data = rp.read_bytes()
    except Exception:
        return None
    if name.endswith(".jpg.enc"):
        fernet = _load_capture_fernet()
        if fernet is None:
            return None  # 키 없으면 복호화 불가(잠금)
        try:
            data = fernet.decrypt(data)
        except Exception:
            return None
    return data


def get_camera_profiles(profiles_path: Path | None = None) -> dict[str, Any]:
    """카메라별 탐지 프로파일(도메인·활성 이벤트) 읽기(camera_profiles.yaml)."""
    cfg = _load_yaml(profiles_path or _CAMERA_PROFILES)
    cams = cfg.get("cameras")
    out: dict[str, Any] = {}
    if isinstance(cams, dict):
        for cid, c in cams.items():
            if isinstance(c, dict):
                out[str(cid)] = {
                    "domain": c.get("domain"),
                    "enabled": bool(c.get("enabled", False)),
                    "enabled_events": [str(e) for e in (c.get("enabled_events") or [])],
                }
    return {"cameras": out}


def set_camera_profile(
    camera_id: str,
    domain: str,
    enabled_events: list[str],
    profiles_path: Path | None = None,
    policies_path: Path | None = None,
) -> dict[str, Any]:
    """카메라 탐지 프로파일 저장(도메인·활성 이벤트).

    설계:
        - 도메인 검증: profiles.<domain> 존재 여부.
        - 이벤트 검증: '카탈로그 전체(이벤트 정책의 모든 프로파일에 정의된 사건)'에 속하는지.
          도메인은 프리셋 역할만 하고 사용자는 도메인 외의 사건도 자유 추가 가능.
        - 미정의 이벤트는 조용히 드롭하지 않고 거부(예외→라우트 4xx).
    """
    clean = validate_event_selection(domain, enabled_events, policies_path)
    path = profiles_path or _CAMERA_PROFILES
    cfg = _load_yaml_rt(path)
    cams = cfg.get("cameras")
    if not isinstance(cams, dict):
        cams = {}
        cfg["cameras"] = cams
    existing = cams.get(camera_id)
    cam: dict[str, Any] = existing if isinstance(existing, dict) else {}
    cam["domain"] = domain
    cam["enabled_events"] = clean
    cams[camera_id] = cam
    _write_yaml(path, cfg)
    return {"camera_id": camera_id, "domain": domain, "enabled_events": clean}


def get_system_metrics() -> dict[str, Any]:
    """시스템 리소스(CPU/메모리/디스크/GPU) 스냅샷. psutil/nvidia 의존은 지연 import·실패 시 {}."""
    try:
        from src.utils.system_metrics import collect_metrics

        return collect_metrics().to_dict()
    except Exception:
        return {}


def get_alert_preferences(prefs_path: Path | None = None) -> dict[str, Any]:
    """알림 환경설정 읽기(이메일 임계값 + 이벤트별 발송 on/off). SMTP 비밀은 env 전용이라 미포함."""
    cfg = _load_yaml(prefs_path or _ALERT_PREFS)
    threshold = cfg.get("email_alert_level_threshold", "medium")
    if threshold not in _ALERT_LEVELS:
        threshold = "medium"
    per = cfg.get("per_event_email_enabled", {})
    if not isinstance(per, dict):
        per = {}
    return {
        "email_alert_level_threshold": threshold,
        "per_event_email_enabled": {str(k): bool(v) for k, v in per.items()},
    }


def set_alert_preferences(
    threshold: str, per_event: dict[str, Any], prefs_path: Path | None = None
) -> dict[str, Any]:
    """알림 환경설정 저장(검증 후 yaml). SMTP 비밀은 다루지 않는다(env). 주석은 ruamel round-trip로 보존."""
    if threshold not in _ALERT_LEVELS:
        raise ValueError(f"임계값은 {', '.join(_ALERT_LEVELS)} 중 하나여야 합니다.")
    if not isinstance(per_event, dict):
        raise ValueError("per_event_email_enabled는 객체(맵)여야 합니다.")
    clean = {str(k)[:128]: bool(v) for k, v in per_event.items()}
    if len(clean) > 500:
        raise ValueError("이벤트 항목이 너무 많습니다(최대 500).")
    path = prefs_path or _ALERT_PREFS
    cfg = _load_yaml_rt(path)  # 다른 키 + 주석(SMTP 안내 등) 보존(ruamel round-trip)
    cfg["email_alert_level_threshold"] = threshold
    cfg["per_event_email_enabled"] = clean
    _write_yaml(path, cfg)
    return {
        "email_alert_level_threshold": threshold,
        "per_event_email_enabled": clean,
        "enabled_count": sum(1 for v in clean.values() if v),
    }


def config_sanitized(config: Any) -> dict[str, Any]:
    """대시보드/프론트가 쓰는 비밀 제거 설정 뷰. 큐레이션 + 마스킹(원본 덤프 금지)."""
    det = config.get_detection_config() if hasattr(config, "get_detection_config") else {}
    alerts = config.get_alert_config() if hasattr(config, "get_alert_config") else {}
    vlm = config.get("models", "vlm", default={}) if hasattr(config, "get") else {}
    return {
        "detection": {
            "skip_frames": det.get("skip_frames"),
            "alert_cooldown_sec": det.get("alert_cooldown_sec"),
            "target_classes": det.get("target_classes"),
            "vlm_trigger_classes": det.get("vlm_trigger_classes"),
        },
        "alerts": {
            "enabled": bool(alerts.get("enabled", True)),
            "save_images": bool(alerts.get("save_images", True)),
            "blur_faces": bool(alerts.get("blur_faces", True)),
            "encrypt_images": bool(alerts.get("encrypt_images", True)),
            "image_retention_days": alerts.get("image_retention_days"),
        },
        "vlm": {
            "enabled": bool((vlm or {}).get("enabled", False)),
            "provider": (vlm or {}).get("provider"),
            "model_name": (vlm or {}).get("model_name"),
            # base_url은 자격증명 포함 가능 → 마스킹
            "base_url": mask_url((vlm or {}).get("base_url", "") or ""),
        },
    }
