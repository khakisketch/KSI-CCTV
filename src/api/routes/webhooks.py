"""Webhook 수신 엔드포인트 관리 API (P2-T3, U12 register 팩토리 스타일).

6종 엔드포인트(list/create/update/delete/rotate/test) + 이벤트×채널 매트릭스
읽기/쓰기(T2 리뷰 M-1 이관 — 값은 list[string] 강제). secret은 create·rotate
응답에서만 1회 평문, 그 외 마스킹(`***`). 감사에는 URL만 기록하고 secret은
절대 기록하지 않는다. 저장소 `config/webhooks.yaml`은 0o600, 쓰기는 ruamel
round-trip으로 주석을 보존한다(service.py alert-preferences 패턴 재사용).

`load_webhook_endpoints()`는 T2 AlertManager의 `webhook_endpoint_provider`
(`Callable[[], list[WebhookEndpoint]]`)에 그대로 주입 가능한 리더이다.
"""
from __future__ import annotations

import contextlib
import logging
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator
from ruamel.yaml import YAML as _RuamelYAML

from src.alerts.webhook import send_webhook
from src.api import service
from src.events.models import (
    NotificationAttempt,
    NotificationChannel,
    WebhookEndpoint,
    validate_webhook_url,
)

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parents[3]
_WEBHOOKS_FILE = _ROOT / "config" / "webhooks.yaml"
_PREFS_FILE = _ROOT / "config" / "alert_preferences.yaml"

_WEBHOOKS_ENV = "KSI_CCTV_WEBHOOKS_FILE"
_PREFS_ENV = "KSI_CCTV_ALERT_PREFS_FILE"

_STORE_MODE = 0o600
_TEST_TIMEOUT_SEC = 10.0
_ALLOWED_CHANNELS = ("email", "webhook")
_MAX_MATRIX_ENTRIES = 500
_MAX_KEY_LEN = 128

# service.py와 동일한 ruamel round-trip 설정(주석 보존).
_yaml_rt = _RuamelYAML()
_yaml_rt.preserve_quotes = True
_yaml_rt.indent(mapping=2, sequence=4, offset=2)
_yaml_rt.width = 4096


def _webhooks_path() -> Path:
    override = os.environ.get(_WEBHOOKS_ENV, "").strip()
    return Path(override) if override else Path(_WEBHOOKS_FILE)


def _prefs_path() -> Path:
    override = os.environ.get(_PREFS_ENV, "").strip()
    return Path(override) if override else Path(_PREFS_FILE)


def _load_yaml_rt(path: Path):
    """주석 보존 round-trip 로드. 없거나 깨졌으면 빈 CommentedMap."""
    from ruamel.yaml.comments import CommentedMap

    if not path.exists():
        return CommentedMap()
    try:
        with open(path, encoding="utf-8") as f:
            data = _yaml_rt.load(f)
        return data if isinstance(data, dict) else CommentedMap()
    except Exception:
        return CommentedMap()


def _fsync_dir(directory: Path) -> None:
    """rename 내구성용 디렉터리 fsync — 실패해도 쓰기 자체는 완료(best-effort)."""
    try:
        dir_fd = os.open(str(directory), os.O_DIRECTORY)
    except OSError:
        return
    try:
        try:
            os.fsync(dir_fd)
        except OSError as exc:
            logger.debug("디렉터리 fsync 생략: %s", exc)
    finally:
        os.close(dir_fd)


def _atomic_yaml_dump(path: Path, data: Any, *, mode: int | None) -> None:
    """원자 저장: temp 작성(fsync) → chmod → rename. 중단 시 구 파일 보존(I-2).

    ruamel 덤프는 temp에만 쓰므로 주석 보존과 충돌하지 않는다.
    temp는 동일 디렉터리에 두어 rename이 원자적이다.
    temp는 mkstemp(0o600)로 생성되어 시크릿 노출 window가 없다(M-1).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _yaml_rt.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    _fsync_dir(path.parent)


def _write_yaml_600(path: Path, data: Any) -> None:
    """ruamel 덤프 + 0o600(시크릿 포함 저장소) — 원자 패턴."""
    _atomic_yaml_dump(path, data, mode=_STORE_MODE)


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _endpoint_to_record(ep: WebhookEndpoint) -> dict[str, Any]:
    return {
        "id": ep.id,
        "name": ep.name,
        "url": ep.url,
        "secret": ep.secret,
        "enabled": bool(ep.enabled),
        "created_at": ep.created_at.isoformat(),
        "rotated_at": ep.rotated_at.isoformat() if ep.rotated_at else None,
    }


def _record_to_endpoint(item: Any) -> WebhookEndpoint | None:
    """저장 레코드 1건 파싱 — 깨진 항목은 None(호출측이 warn+스킵)."""
    if not isinstance(item, dict):
        return None
    try:
        raw_id = item.get("id")
        if not isinstance(raw_id, str) or not raw_id:
            return None
        return WebhookEndpoint(
            id=raw_id,
            name=str(item.get("name") or ""),
            url=str(item.get("url") or ""),
            secret=str(item.get("secret") or ""),
            enabled=bool(item.get("enabled", True)),
            created_at=_parse_time(item.get("created_at")),
            rotated_at=_parse_time(item["rotated_at"]) if item.get("rotated_at") else None,
        )
    except Exception:
        return None


def load_webhook_endpoints(path: Path | None = None) -> list[WebhookEndpoint]:
    """엔드포인트 목록 로드 — T2 `webhook_endpoint_provider` 주입용 리더.

    깨진 항목은 warn 후 스킵한다(단일 오류 항목이 전체 알림 경로를 막지 않음).
    """
    target = Path(path) if path is not None else _webhooks_path()
    cfg = _load_yaml_rt(target)
    items = cfg.get("endpoints", [])
    if not isinstance(items, list):
        return []
    out: list[WebhookEndpoint] = []
    for item in items:
        ep = _record_to_endpoint(item)
        if ep is None:
            logger.warning("webhook 저장소 깨진 항목 스킵: %s", target)
            continue
        out.append(ep)
    return out


def _save_endpoints(endpoints: list[WebhookEndpoint], path: Path | None = None) -> None:
    target = Path(path) if path is not None else _webhooks_path()
    cfg = _load_yaml_rt(target)
    cfg["version"] = 1
    cfg["endpoints"] = [_endpoint_to_record(ep) for ep in endpoints]
    _write_yaml_600(target, cfg)


def _find(endpoints: list[WebhookEndpoint], endpoint_id: str) -> WebhookEndpoint | None:
    for ep in endpoints:
        if ep.id == endpoint_id:
            return ep
    return None


def _clean_matrix(raw: Any) -> dict[str, list[str]]:
    """per_event_channels 검증 — 값이 list[string]이 아니면 422(T2 M-1)."""
    if not isinstance(raw, dict):
        raise HTTPException(status_code=422, detail="channels는 객체(맵)여야 합니다.")
    if len(raw) > _MAX_MATRIX_ENTRIES:
        raise HTTPException(
            status_code=422,
            detail=f"채널 매핑이 너무 많습니다(최대 {_MAX_MATRIX_ENTRIES}).",
        )
    clean: dict[str, list[str]] = {}
    for key, value in raw.items():
        name = str(key)
        if len(name) > _MAX_KEY_LEN:
            raise HTTPException(
                status_code=422,
                detail=f"이벤트 키가 너무 깁니다(최대 {_MAX_KEY_LEN}자).",
            )
        if not isinstance(value, list):
            raise HTTPException(
                status_code=422,
                detail=f"{name!r}의 채널 값은 문자열 목록이어야 합니다(문자열 오타 방지).",
            )
        items: list[str] = []
        for item in value:
            if not isinstance(item, str) or item not in _ALLOWED_CHANNELS:
                raise HTTPException(
                    status_code=422,
                    detail=f"{name!r}의 채널은 {', '.join(_ALLOWED_CHANNELS)} 중 하나여야 합니다.",
                )
            if item not in items:
                items.append(item)
        clean[name] = items
    return clean


def _read_matrix(path: Path | None = None) -> dict[str, list[str]]:
    target = Path(path) if path is not None else _prefs_path()
    cfg = _load_yaml_rt(target)
    matrix = cfg.get("per_event_channels", {})
    return dict(matrix) if isinstance(matrix, dict) else {}


class WebhookCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=1, max_length=2048)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name은 비어 있을 수 없습니다.")
        return cleaned


class WebhookUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("name은 비어 있을 수 없습니다.")
        return cleaned


class ChannelsBody(BaseModel):
    channels: dict[str, Any]
    confirm: bool = False


def _public_with_secret(ep: WebhookEndpoint, secret: str) -> dict[str, Any]:
    """create·rotate 응답 전용 — 1회 평문 secret 포함."""
    public = ep.to_public_dict()
    public["secret"] = secret
    return public


def register(app, auth) -> None:
    @app.get("/api/webhooks", tags=["webhooks"], dependencies=auth)
    def webhooks_list() -> dict[str, Any]:
        endpoints = load_webhook_endpoints()
        return {"count": len(endpoints), "endpoints": [ep.to_public_dict() for ep in endpoints]}

    @app.post("/api/webhooks", tags=["webhooks"], dependencies=auth)
    def webhooks_create(body: WebhookCreateBody) -> dict[str, Any]:
        try:
            validate_webhook_url(body.url)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        endpoints = load_webhook_endpoints()
        taken = {ep.id for ep in endpoints}
        endpoint_id = secrets.token_hex(8)
        while endpoint_id in taken:
            endpoint_id = secrets.token_hex(8)
        secret = secrets.token_hex(32)
        ep = WebhookEndpoint(
            id=endpoint_id,
            name=body.name,
            url=body.url,
            secret=secret,
            enabled=True,
            created_at=datetime.now(timezone.utc),
        )
        endpoints.append(ep)
        _save_endpoints(endpoints)
        service.write_audit(
            "webhook_created", endpoint_id=ep.id, name=ep.name, url=ep.url
        )
        return {"endpoint": _public_with_secret(ep, secret)}

    # 주의: 정적 경로(/channels)는 {endpoint_id} 변수 경로보다 먼저 등록해야 한다.
    # 늦게 등록하면 "channels"가 endpoint_id로 매칭되어 404가 된다(M-5 회귀 테스트).
    @app.get("/api/webhooks/channels", tags=["webhooks"], dependencies=auth)
    def webhooks_channels_get() -> dict[str, Any]:
        return {"channels": _read_matrix()}

    @app.put("/api/webhooks/channels", tags=["webhooks"], dependencies=auth)
    def webhooks_channels_put(body: ChannelsBody) -> dict[str, Any]:
        clean = _clean_matrix(body.channels)
        # R9 I-1: {}뿐 아니라 전부-빈목록({fire:[]} 등)도 전체 삭제이므로 confirm:true 필수
        # (frontend shouldConfirmChannelsSave와 정합 — keys.every(length === 0)).
        if (not clean or all(len(v) == 0 for v in clean.values())) and not body.confirm:
            raise HTTPException(
                status_code=422,
                detail="매트릭스 전체 삭제는 confirm:true 확인이 필요합니다.",
            )
        path = _prefs_path()
        cfg = _load_yaml_rt(path)
        cfg["per_event_channels"] = clean
        _atomic_yaml_dump(path, cfg, mode=None)
        service.write_audit(
            "webhook_channels_saved", event_count=len(clean), events=sorted(clean)
        )
        return {"channels": clean, "event_count": len(clean)}

    @app.put("/api/webhooks/{endpoint_id}", tags=["webhooks"], dependencies=auth)
    def webhooks_update(endpoint_id: str, body: WebhookUpdateBody) -> dict[str, Any]:
        if body.name is None and body.url is None and body.enabled is None:
            raise HTTPException(status_code=422, detail="변경 항목이 없습니다.")
        endpoints = load_webhook_endpoints()
        ep = _find(endpoints, endpoint_id)
        if ep is None:
            raise HTTPException(status_code=404, detail="웹훅 엔드포인트를 찾을 수 없습니다.")
        changed: list[str] = []
        if body.name is not None:
            ep.name = body.name
            changed.append("name")
        if body.url is not None:
            try:
                validate_webhook_url(body.url)
            except ValueError as e:
                raise HTTPException(status_code=422, detail=str(e)) from e
            ep.url = body.url
            changed.append("url")
        if body.enabled is not None:
            ep.enabled = bool(body.enabled)
            changed.append("enabled")
        _save_endpoints(endpoints)
        service.write_audit(
            "webhook_updated", endpoint_id=ep.id, name=ep.name, url=ep.url, fields=changed
        )
        return {"endpoint": ep.to_public_dict()}

    @app.delete("/api/webhooks/{endpoint_id}", tags=["webhooks"], dependencies=auth)
    def webhooks_delete(endpoint_id: str) -> dict[str, Any]:
        endpoints = load_webhook_endpoints()
        ep = _find(endpoints, endpoint_id)
        if ep is None:
            raise HTTPException(status_code=404, detail="웹훅 엔드포인트를 찾을 수 없습니다.")
        _save_endpoints([e for e in endpoints if e.id != endpoint_id])
        service.write_audit(
            "webhook_deleted", endpoint_id=ep.id, name=ep.name, url=ep.url
        )
        return {"ok": True, "id": endpoint_id}

    @app.post("/api/webhooks/{endpoint_id}/rotate", tags=["webhooks"], dependencies=auth)
    def webhooks_rotate(endpoint_id: str) -> dict[str, Any]:
        endpoints = load_webhook_endpoints()
        ep = _find(endpoints, endpoint_id)
        if ep is None:
            raise HTTPException(status_code=404, detail="웹훅 엔드포인트를 찾을 수 없습니다.")
        secret = secrets.token_hex(32)
        ep.secret = secret
        ep.rotated_at = datetime.now(timezone.utc)
        _save_endpoints(endpoints)
        service.write_audit(
            "webhook_rotated", endpoint_id=ep.id, name=ep.name, url=ep.url
        )
        return {"endpoint": _public_with_secret(ep, secret)}

    @app.post("/api/webhooks/{endpoint_id}/test", tags=["webhooks"], dependencies=auth)
    def webhooks_test(endpoint_id: str) -> dict[str, Any]:
        endpoints = load_webhook_endpoints()
        ep = _find(endpoints, endpoint_id)
        if ep is None:
            raise HTTPException(status_code=404, detail="웹훅 엔드포인트를 찾을 수 없습니다.")
        if not ep.enabled:
            raise HTTPException(
                status_code=409, detail="비활성 엔드포인트는 테스트 발송할 수 없습니다."
            )
        now = datetime.now(timezone.utc)
        attempt = NotificationAttempt(
            decision_id=f"webhook_test_{ep.id}",
            channel=NotificationChannel.WEBHOOK,
            recipient=ep.id,
            idempotency_key=f"webhook_test_{ep.id}_{int(now.timestamp())}",
            payload={
                "event_id": f"webhook_test_{ep.id}",
                "event_type": "webhook_test",
                "camera_id": "console",
                "timestamp": now.isoformat(),
            },
        )
        ok = send_webhook(attempt, ep, _TEST_TIMEOUT_SEC, message_type="test")
        service.write_audit(
            "webhook_tested", endpoint_id=ep.id, name=ep.name, url=ep.url, ok=bool(ok)
        )
        return {"ok": bool(ok), "endpoint_id": ep.id}

