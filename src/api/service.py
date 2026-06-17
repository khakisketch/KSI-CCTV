"""엔진 API 데이터 접근 계층 — 기존 SSOT(config/이벤트/모델/정책)를 읽어 안전하게 반환.

라우트와 분리해 단위 테스트 가능하게 한다. 모든 외부 노출 텍스트는 시크릿 마스킹(mask_text/mask_url)을
거친다 — RTSP/SMTP 자격증명·VLM 자유서술 PII가 API로 새지 않게(보안 하드닝 보존).
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import re
import shutil
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from src.alerts.alert_manager import parse_alert_log_line
from src.utils.config import mask_text, mask_url

_ROOT = Path(__file__).resolve().parents[2]
_MODEL_PROFILES = _ROOT / "config" / "model_profiles.yaml"
_EVENT_POLICIES = _ROOT / "config" / "event_policies.yaml"
_CAPTURES_DIR = _ROOT / "data" / "captures"
_ALERT_PREFS = _ROOT / "config" / "alert_preferences.yaml"
_ALERT_LEVELS = ("low", "medium", "high", "critical")
_DATE_RE = re.compile(r"^\d{8}$")  # YYYYMMDD
_SAFE_SEG = re.compile(r"^[A-Za-z0-9._-]+$")  # 카메라/파일명(슬래시·.. 불허)
# 감사 로그(JSONL) — Streamlit 대시보드와 동일 파일을 공유해 단일 감사 트레일을 만든다.
_AUDIT_LOG = _ROOT / "logs" / "dashboard_audit.jsonl"
_AUDIT_MAX_BYTES = 5 * 1024 * 1024
_AUDIT_TEXT_FIELDS = ("error", "detail", "original_filename", "model_name")
_ALERTS_LOG = _ROOT / "logs" / "alerts.log"

EVENTS_MAX_LIMIT = 1000

# 자유서술/식별 텍스트만 마스킹 대상(구조·수치 필드는 보존 — 오탐 치환 방지).
_EVENT_TEXT_FIELDS = ("description", "vlm_raw_response", "vlm_recommended_action", "rule_name")


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def list_events(
    limit: int = 100,
    event_type: str | None = None,
    camera_id: str | None = None,
    alerts_log: Path | None = None,
) -> list[dict[str, Any]]:
    """alerts.log에서 최근 이벤트를 파싱해 반환(최신순). 자유서술 필드는 마스킹.

    append-only 로그를 deque(maxlen)로 스트리밍해 메모리를 제한한다(장기운영 로그 비대화 대비).
    """
    path = alerts_log or _ALERTS_LOG
    limit = max(1, min(int(limit), EVENTS_MAX_LIMIT))
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
            if event_type and ev.get("event_type") != event_type:
                continue
            if camera_id and ev.get("camera_id") != camera_id:
                continue
            buf.append(_mask_event(ev))
    events = list(buf)
    events.reverse()  # 최신순
    return events[:limit]


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


async def stream_events(
    is_disconnected: Callable[[], Awaitable[bool]],
    alerts_log: Path | None = None,
    poll_interval: float = 1.0,
    heartbeat_every: int = 15,
) -> AsyncIterator[str]:
    """alerts.log의 신규 이벤트를 SSE 프레임 문자열로 yield(파일 끝부터 tail).

    초기 목록은 list_events로 SSR되므로 여기서는 파일 끝 이후의 신규 라인만 push한다.
    파일 트렁케이트/로테이션 시 위치를 재설정하고, heartbeat 주석으로 연결을 유지한다.
    list_events와 동일한 파싱·마스킹(_mask_event)을 재사용한다.
    """
    path = alerts_log or _ALERTS_LOG
    pos = path.stat().st_size if path.exists() else 0
    pending = ""
    beats = 0
    # 연결 즉시 첫 바이트를 흘려보내 헤더 flush + 클라 onopen을 빠르게 트리거(첫 이벤트/heartbeat
    # 까지 기다리지 않게 — "실시간 수신 중" 표시가 즉시 뜬다).
    yield ": connected\n\n"
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
                        payload = json.dumps(_mask_event(ev), ensure_ascii=False)
                        yield f"event: alert\ndata: {payload}\n\n"
        except Exception:
            pass
        beats += 1
        if beats % max(1, heartbeat_every) == 0:
            yield ": keepalive\n\n"
        await asyncio.sleep(poll_interval)


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
            })
        profiles_out[pname] = events
    return {"profiles": profiles_out}


MAX_PROMPTS = 100
MAX_PROMPT_LEN = 64


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)


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
    cfg = _load_yaml(path)
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
    cfg = _load_yaml(path)
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
    cfg = _load_yaml(path)
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
    file_bytes: bytes,
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

    result = upload_validator.upload_model(
        file_bytes,
        original_filename,
        metadata={"model_name": model_name, "classes": classes, "input_size": input_size},
    )
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


def write_audit(event: str, audit_log: Path | None = None, **fields: Any) -> None:
    """감사 로그(JSONL)에 한 줄 append(best-effort — 실패해도 주 기능을 막지 않음).

    Streamlit과 동일 파일을 공유해 통합 감사 트레일을 만든다. 엔진 API 발 작업은 source='api'.
    크기 초과 시 최신 절반만 유지(간단 회전, Streamlit 로직과 동일 개념)."""
    path = audit_log or _AUDIT_LOG
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size >= _AUDIT_MAX_BYTES:
            lines = path.read_text(encoding="utf-8").splitlines()
            keep = max(1, len(lines) // 2)
            path.write_text("\n".join(lines[-keep:]) + "\n", encoding="utf-8")
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "source": "api",
            **fields,
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _mask_audit(entry: dict[str, Any]) -> dict[str, Any]:
    masked = dict(entry)
    for key in _AUDIT_TEXT_FIELDS:
        val = masked.get(key)
        if isinstance(val, str) and val:
            masked[key] = mask_text(val)
    return masked


def list_audit(limit: int = 200, audit_log: Path | None = None) -> list[dict[str, Any]]:
    """감사 로그를 최신순으로 반환(마스킹). JSONL 파싱 실패 줄은 skip."""
    path = audit_log or _AUDIT_LOG
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if isinstance(entry, dict):
                out.append(_mask_audit(entry))
    out.reverse()
    return out[: max(1, min(int(limit), 1000))]


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
    """알림 환경설정 저장(검증 후 yaml). SMTP 비밀은 다루지 않는다(env). 주석은 safe_dump로 소실(ruamel 후속)."""
    if threshold not in _ALERT_LEVELS:
        raise ValueError(f"임계값은 {', '.join(_ALERT_LEVELS)} 중 하나여야 합니다.")
    if not isinstance(per_event, dict):
        raise ValueError("per_event_email_enabled는 객체(맵)여야 합니다.")
    clean = {str(k)[:128]: bool(v) for k, v in per_event.items()}
    if len(clean) > 500:
        raise ValueError("이벤트 항목이 너무 많습니다(최대 500).")
    path = prefs_path or _ALERT_PREFS
    cfg = _load_yaml(path)  # 다른 키 보존(SMTP 주석은 소실 — 알려진 한계)
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
            "encrypt_images": bool(alerts.get("encrypt_images", False)),
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
