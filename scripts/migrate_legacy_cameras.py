#!/usr/bin/env python3
"""Legacy cameras.json -> managed config/cameras/ one-shot migration.

Reads a legacy flat camera list (``config/cameras.json``) with
``AppConfig.load_cameras`` (environment expansion included) and writes
managed records with ``CameraRepository.create``. Credentials embedded in
stream URLs (``user:pass@``) are split into ``CredentialStore`` and never
printed: all output URLs go through ``mask_url``.

Default is dry-run (no filesystem writes). Pass ``--apply`` to record.

Exit codes: 0 = success (skips are warning summaries), 2 = fatal
(source missing/unreadable, managed store not empty).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.cameras.credentials import CredentialStore
from src.cameras.models import CameraConfig, CameraInput
from src.cameras.repository import CameraRepository
from src.utils.config import AppConfig, mask_url

FATAL_NON_EMPTY = "관리 저장소가 비어있지 않음 — 수동 병합 필요"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Migrate legacy config/cameras.json to managed config/cameras/.",
    )
    parser.add_argument("--source", default="config/cameras.json")
    parser.add_argument("--dest", default="config/cameras")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually record to dest. Without it, dry-run only (no writes).",
    )
    return parser.parse_args(argv)


def split_userinfo(url: str) -> tuple[str, str | None, str | None]:
    """Split ``user:pass@`` out of a URL. Returns (clean_url, user, password)."""
    parsed = urlsplit(url)
    username = unquote(parsed.username) if parsed.username else None
    password = unquote(parsed.password) if parsed.password else None
    if not username and not password:
        return url, None, None
    host = parsed.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if parsed.port:
        host = f"{host}:{parsed.port}"
    clean = urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    return clean, username, password


def normalize_zones(raw: Any) -> tuple[list[Any], str | None]:
    """Normalize legacy zones to a CameraInput-compatible list."""
    if raw is None:
        return [], None
    if isinstance(raw, list):
        return raw, None
    if isinstance(raw, dict) and not raw:
        return [], None
    return [], "zones 형식을 해석할 수 없어 비워둠"


def build_input(raw: dict[str, Any]) -> tuple[CameraInput, str | None, str | None, str | None]:
    """Build a CameraInput from a legacy entry.

    Returns (input, username, password, zone_warning). Raises ValueError
    with a secret-free reason when the entry must be skipped.
    """
    cam_id = raw.get("id")
    if not cam_id:
        raise ValueError("id 누락")
    stream_url = raw.get("stream_url") or raw.get("rtsp_url")
    if not stream_url or not isinstance(stream_url, str):
        raise ValueError("stream_url 누락")
    if "${" in stream_url:
        raise ValueError("stream_url 환경변수 미설정")
    clean_url, username, password = split_userinfo(stream_url)
    resolution = raw.get("resolution")
    if isinstance(resolution, list):
        resolution = tuple(resolution)
    zones, zone_warning = normalize_zones(raw.get("zones"))
    try:
        item = CameraInput(
            id=cam_id,
            name=raw.get("name"),
            location=raw.get("location", "") or "",
            rtsp_url=clean_url,
            enabled=bool(raw.get("enabled", True)),
            fps=raw.get("fps", 10),
            resolution=resolution,
            profile=raw.get("profile") or raw.get("detection_type") or "general",
            enabled_events=list(raw.get("enabled_events", []) or []),
            zones=zones,
            username=username,
            password=password,
        )
    except Exception as exc:
        raise ValueError(f"검증 실패 ({type(exc).__name__})") from exc
    return item, username, password, zone_warning


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.source)
    dest = Path(args.dest)

    if not source.exists():
        print(f"치명 오류: 소스 파일 없음: {source}", file=sys.stderr)
        return 2

    try:
        legacy = AppConfig().load_cameras(str(source))
    except Exception as exc:
        print(f"치명 오류: 소스 로드 실패: {type(exc).__name__}", file=sys.stderr)
        return 2

    repo = CameraRepository(dest)
    try:
        _, existing = repo.list()
    except ValueError as exc:
        print(f"치명 오류: 목적지 저장소를 읽을 수 없음: {type(exc).__name__}", file=sys.stderr)
        return 2
    if existing:
        print(f"치명 오류: {FATAL_NON_EMPTY} (dest={dest}, 기존 {len(existing)}대)", file=sys.stderr)
        return 2

    mode = "apply" if args.apply else "dry-run"
    store = CredentialStore(dest) if args.apply else None

    planned = 0
    migrated = 0
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for raw in legacy:
        if not isinstance(raw, dict):
            skipped.append(("unknown", "카메라 항목 형식 오류"))
            continue
        cam_id = str(raw.get("id") or "unknown")
        masked = mask_url(str(raw.get("stream_url") or raw.get("rtsp_url") or ""))
        if cam_id in seen:
            skipped.append((cam_id, "중복 id"))
            print(f"[skip] {cam_id} url={masked} reason=중복 id")
            continue
        seen.add(cam_id)
        try:
            item, username, password, zone_warning = build_input(raw)
        except ValueError as exc:
            skipped.append((cam_id, str(exc)))
            print(f"[skip] {cam_id} url={masked} reason={exc}")
            continue
        has_creds = username is not None or password is not None
        ref = f"camera:{item.id}" if has_creds else None
        camera = CameraConfig.from_input(item, credential_ref=ref, camera_id=item.id)
        if zone_warning:
            print(f"[warn] {cam_id} {zone_warning}")
        if has_creds:
            print(f"[plan] {cam_id} url={masked} -> {mask_url(camera.rtsp_url)} (자격증명 분리 저장)")
        else:
            print(f"[plan] {cam_id} url={mask_url(camera.rtsp_url)} -> 생성 (자격증명 없음)")
        if not args.apply:
            planned += 1
            continue
        try:
            parsed = urlsplit(camera.rtsp_url)
            if has_creds and store is not None:
                store.put(
                    ref,
                    username,
                    password,
                    CredentialStore.target_scope([parsed.hostname or ""], parsed.port or 554),
                )
            repo.create(camera, expected_revision=None)
        except Exception as exc:
            skipped.append((cam_id, f"기록 실패 ({type(exc).__name__})"))
            print(f"[skip] {cam_id} url={mask_url(camera.rtsp_url)} reason=기록 실패 ({type(exc).__name__})")
            continue
        migrated += 1
        print(f"[ok] {cam_id} url={mask_url(camera.rtsp_url)}")

    done = migrated if args.apply else planned
    print(f"요약 mode={mode} 대상={len(legacy)}대 생성={done}대 건너뜀={len(skipped)}대")
    for cam_id, reason in skipped:
        print(f"  - {cam_id}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
