"""카메라/ONVIF 그룹 라우트 (U12c 분할). 경로·동작 무변경 이동."""
from __future__ import annotations

import os
import secrets
import threading
import time
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Literal
from urllib.parse import urlsplit

from fastapi import HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field, model_validator

from src.api import service
from src.cameras import (
    CameraAlreadyExists,
    CameraConfig,
    CameraInput,
    CameraLimitReached,
    CameraZone,
    CredentialStore,
    MediaMTXError,
    RevisionConflict,
    RtspProbe,
    compile_plan,
)


class CameraProfileBody(BaseModel):
    """카메라 탐지 프로파일 저장 요청(도메인 + 활성 이벤트)."""

    domain: str = Field(..., description="event_policies.yaml의 도메인 프로파일명")
    enabled_events: list[str] = Field(default_factory=list)
    expected_revision: int | None = Field(default=None, ge=0)


class CameraProbeBody(BaseModel):
    rtsp_url: str = Field(min_length=1, max_length=2048)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=1024)
    verify_stream: bool = True


class CameraDraftProbeBody(CameraProbeBody):
    clear_credentials: bool = False

    @model_validator(mode="after")
    def valid_credentials(self) -> CameraDraftProbeBody:
        if self.clear_credentials and (self.username is not None or self.password is not None):
            raise ValueError("clear_credentials cannot be combined with username or password")
        return self


class CameraZonesBody(BaseModel):
    zones: list[CameraZone] = Field(default_factory=list, max_length=32)
    expected_revision: int | None = Field(default=None, ge=0)


class OnvifProfilesBody(BaseModel):
    discovery_id: str = Field(min_length=16, max_length=128)
    username: str | None = Field(default=None, max_length=256)
    password: str | None = Field(default=None, max_length=1024)


class CameraDiscoveryBody(BaseModel):
    protocol: Literal["onvif", "gateway", "all"] = "all"


def register(app, auth) -> None:
    onvif_discovery_lock = threading.RLock()
    onvif_discoveries: dict[str, tuple[float, str, int]] = {}

    def remember_onvif_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.monotonic()
        with onvif_discovery_lock:
            expired = [token for token, (expires_at, _, _) in onvif_discoveries.items() if expires_at <= now]
            for token in expired:
                del onvif_discoveries[token]
            remembered = []
            for device in devices:
                host = device.get("host")
                port = device.get("onvif_port", 80)
                if not isinstance(host, str) or not host or not isinstance(port, int) or not 1 <= port <= 65535:
                    continue
                token = secrets.token_urlsafe(24)
                onvif_discoveries[token] = (now + 120, host, port)
                remembered.append({**device, "discovery_id": token})
            return remembered

    def discovered_onvif_target(discovery_id: str) -> tuple[str, int]:
        now = time.monotonic()
        with onvif_discovery_lock:
            entry = onvif_discoveries.get(discovery_id)
            if entry is None or entry[0] <= now:
                onvif_discoveries.pop(discovery_id, None)
                raise HTTPException(status_code=400, detail="카메라 검색 결과가 만료되었습니다. 다시 검색하세요.")
            return entry[1], entry[2]

    def _serialized_camera_mutation(func):
        @wraps(func)
        def wrapped(*args, **kwargs):
            with app.state.camera_runtime_lock:
                return func(*args, **kwargs)

        return wrapped

    def _apply_camera_runtime(
        camera: CameraConfig,
        validated_addresses: list[str] | None = None,
    ) -> tuple[bool, str | None, bool, bool | None]:
        addresses = validated_addresses or _validate_camera_target(camera.rtsp_url)
        if not camera.enabled:
            return _remove_camera_runtime(camera.id)
        pipeline_obj = app.state.pipeline
        credential = app.state.camera_credentials.get(camera.credential_ref) or {}
        target_scope = CredentialStore.target_scope(
            addresses,
            urlsplit(camera.rtsp_url).port or 554,
        )
        stored_scope = credential.get("target_scope")
        if stored_scope and stored_scope != target_scope:
            return False, "credential_target_changed", False, None
        if credential and not stored_scope:
            return False, "credential_target_unverified", False, None
        # The persisted URL stays hostname-based. The mounted capture uses a
        # validated address so later DNS rebinding cannot redirect it.
        stream_url = RtspProbe.pin_url(
            RtspProbe.with_credentials(
                camera.rtsp_url,
                credential.get("username"),
                credential.get("password"),
            ),
            addresses[0],
        )
        zones = {
            zone.id: {
                "type": zone.type,
                "points": [[point.x, point.y] for point in zone.points],
                "normalized": True,
                "direction": zone.direction,
            }
            for zone in camera.zones
        }
        profile = {
            "domain": camera.profile or "general",
            "enabled_events": list(camera.enabled_events),
            "events": list(camera.enabled_events),
            "zones": zones,
            "detection_type": camera.profile or "general",
        }
        pipeline_applied = False
        pipeline_error = None
        if pipeline_obj is None or not hasattr(pipeline_obj, "apply_camera"):
            pipeline_error = "pipeline_unavailable"
        else:
            try:
                pipeline_obj.apply_camera(camera.id, stream_url, profile=profile,
                                          target_fps=camera.fps, resize=camera.resolution)
                pipeline_applied = True
            except Exception as exc:
                pipeline_error = type(exc).__name__
        gateway_synced: bool | None = None
        gateway_error = None
        if app.state.mediamtx is not None:
            try:
                app.state.mediamtx.sync(camera.id, stream_url)
                gateway_synced = True
            except MediaMTXError as exc:
                gateway_synced = False
                gateway_error = str(exc)
            except Exception as exc:
                gateway_synced = False
                gateway_error = type(exc).__name__
        errors = ([pipeline_error] if pipeline_error else []) + (
            [f"gateway:{gateway_error}"] if gateway_error else []
        )
        applied = pipeline_applied and gateway_synced is not False
        return applied, ";".join(errors) or None, pipeline_applied, gateway_synced

    def _validate_camera_target(rtsp_url: str) -> list[str]:
        parsed = urlsplit(rtsp_url)
        try:
            addresses = app.state.rtsp_probe.allowed_host(parsed.hostname or "", parsed.port or 554)
        except OSError as exc:
            raise HTTPException(status_code=422, detail="RTSP host could not be resolved") from exc
        if not addresses:
            raise HTTPException(
                status_code=422,
                detail="RTSP target must resolve only to private LAN addresses",
            )
        return addresses

    def _camera_credentials_for_target(
        camera: CameraConfig,
        body: CameraInput | CameraDraftProbeBody,
        addresses: list[str],
    ) -> tuple[str | None, str | None]:
        if body.clear_credentials:
            return None, None
        stored = app.state.camera_credentials.get(camera.credential_ref) or {}
        current = urlsplit(camera.rtsp_url)
        target = urlsplit(body.rtsp_url)
        current_scope = (current.hostname or "", current.port or 554)
        target_scope = (target.hostname or "", target.port or 554)
        resolved_scope = CredentialStore.target_scope(addresses, target.port or 554)
        stored_scope = stored.get("target_scope")
        if (
            current_scope != target_scope
            or (stored_scope and stored_scope != resolved_scope)
            or (stored and not stored_scope)
        ):
            reused = [
                field
                for field in ("username", "password")
                if getattr(body, field) is None and stored.get(field)
            ]
            if reused:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "새 RTSP 장비 또는 포트에는 기존 자격 증명을 재사용할 수 없습니다. "
                        "교체 자격 증명을 입력하거나 인증 정보 제거를 선택하세요."
                    ),
                )
        return (
            body.username if body.username is not None else stored.get("username"),
            body.password if body.password is not None else stored.get("password"),
        )

    def _remove_camera_runtime(camera_id: str) -> tuple[bool, str | None, bool, bool | None]:
        pipeline_obj = app.state.pipeline
        pipeline_removed = False
        pipeline_error = None
        if pipeline_obj is not None and hasattr(pipeline_obj, "remove_camera"):
            try:
                pipeline_removed = bool(pipeline_obj.remove_camera(camera_id))
            except Exception as exc:
                pipeline_error = type(exc).__name__
        gateway_synced: bool | None = None
        gateway_error = None
        if app.state.mediamtx is not None:
            try:
                app.state.mediamtx.delete(camera_id)
                gateway_synced = True
            except MediaMTXError as exc:
                gateway_synced = False
                gateway_error = str(exc)
            except Exception as exc:
                gateway_synced = False
                gateway_error = type(exc).__name__
        errors = ([pipeline_error] if pipeline_error else []) + (
            [f"gateway:{gateway_error}"] if gateway_error else []
        )
        return (not errors, ";".join(errors) or None,
                pipeline_removed, gateway_synced)

    def _invalidate_commissioning_acceptance() -> None:
        app.state.commissioning.invalidate_completion()

    @app.get("/api/cameras", tags=["cameras"], dependencies=auth)
    def cameras() -> dict[str, Any]:
        revision, installed = app.state.camera_repository.list()
        merged = {str(item.get("id")): item for item in service.list_cameras(app.state.config)}
        for camera in installed:
            merged[camera.id] = camera.public_dict()
        items = sorted(merged.values(), key=lambda item: str(item.get("id", "")))
        return {"count": len(items), "cameras": items, "revision": revision}

    @app.post("/api/cameras", tags=["cameras"], dependencies=auth, status_code=201)
    @_serialized_camera_mutation
    def create_camera(body: CameraInput, expected_revision: int | None = Query(None, ge=0)) -> dict[str, Any]:
        repo = app.state.camera_repository
        domain = body.profile or "general"
        try:
            enabled_events = service.validate_event_selection(domain, body.enabled_events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        camera = CameraConfig.from_input(body.model_copy(update={
            "profile": domain,
            "enabled_events": enabled_events,
        }))
        zone_warnings = service.event_zone_warnings(enabled_events, list(camera.zones))
        if zone_warnings:
            raise HTTPException(
                status_code=400,
                detail="영역 설정이 필요한 감지 항목이 있습니다: " + "; ".join(zone_warnings),
            )
        addresses = _validate_camera_target(camera.rtsp_url)
        if camera.enabled:
            stream_test = app.state.rtsp_probe.run(
                camera.rtsp_url,
                username=body.username,
                password=body.password,
                verify_stream=True,
                validated_addresses=addresses,
            )
            if not (stream_test.get("ok") or stream_test.get("connected")):
                raise HTTPException(
                    status_code=400,
                    detail="카메라 등록 전 첫 프레임 검사가 필요합니다. 스트림을 확인한 뒤 다시 시도하세요.",
                )
        _, managed = repo.list()
        legacy_ids = {
            str(item.get("id"))
            for item in app.state.config.load_cameras()
            if isinstance(item, dict) and item.get("id")
        }
        if len(legacy_ids | {item.id for item in managed} | {camera.id}) > repo.MAX_CAMERAS:
            raise HTTPException(
                status_code=409,
                detail=f"total camera limit reached ({repo.MAX_CAMERAS})",
            )
        ref = f"camera:{camera.id}" if not body.clear_credentials and (body.username is not None or body.password is not None) else None
        previous_credential = app.state.camera_credentials.get(ref)
        if ref:
            app.state.camera_credentials.put(
                ref,
                body.username,
                body.password,
                CredentialStore.target_scope(addresses, urlsplit(camera.rtsp_url).port or 554),
            )
            camera = camera.model_copy(update={"credential_ref": ref})

        def restore_new_credential() -> None:
            if not ref:
                return
            if previous_credential is None:
                app.state.camera_credentials.delete(ref)
            else:
                app.state.camera_credentials.put(
                    ref,
                    previous_credential.get("username"),
                    previous_credential.get("password"),
                    previous_credential.get("target_scope"),
                )

        try:
            revision = repo.create(camera, expected_revision, occupied_ids=legacy_ids)
        except RevisionConflict as exc:
            restore_new_credential()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CameraAlreadyExists as exc:
            restore_new_credential()
            raise HTTPException(status_code=409, detail="camera already exists") from exc
        except CameraLimitReached as exc:
            restore_new_credential()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        applied, apply_error, pipeline_applied, gateway_synced = (
            _apply_camera_runtime(camera, addresses) if camera.enabled else (False, None, False, None)
        )
        service.write_audit("camera_created", camera=camera.id, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        _invalidate_commissioning_acceptance()
        return {
            "revision": revision,
            "camera": camera.public_dict(),
            "applied": applied,
            "apply_error": apply_error,
            "pipeline_applied": pipeline_applied,
            "gateway_synced": gateway_synced,
        }

    @app.put("/api/cameras/{camera_id}", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def update_camera(camera_id: str, body: CameraInput, expected_revision: int | None = Query(None, ge=0)) -> dict[str, Any]:
        if body.id is not None and body.id != camera_id:
            raise HTTPException(status_code=400, detail="camera id does not match path")
        repo = app.state.camera_repository
        observed_revision, cameras_at_read = repo.list()
        old = next((camera for camera in cameras_at_read if camera.id == camera_id), None)
        if old is None:
            raise HTTPException(status_code=404, detail="camera not found")
        addresses = _validate_camera_target(body.rtsp_url)
        _camera_credentials_for_target(old, body, addresses)
        domain = body.profile or old.profile or "general"
        try:
            enabled_events = service.validate_event_selection(domain, body.enabled_events)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        ref = None if body.clear_credentials else old.credential_ref
        if not body.clear_credentials and (body.username is not None or body.password is not None):
            # The path is the authority for updates; an omitted body id must
            # never produce a shared camera:None credential reference.
            ref = f"camera:{camera_id}"
        previous_credential = app.state.camera_credentials.get(ref)
        if not body.clear_credentials and (body.username is not None or body.password is not None):
            app.state.camera_credentials.put(
                ref,
                body.username,
                body.password,
                CredentialStore.target_scope(addresses, urlsplit(body.rtsp_url).port or 554),
            )

        def restore_update_credential() -> None:
            if body.clear_credentials or (body.username is None and body.password is None):
                return
            if previous_credential is None:
                app.state.camera_credentials.delete(ref)
            else:
                app.state.camera_credentials.put(
                    ref,
                    previous_credential.get("username"),
                    previous_credential.get("password"),
                    previous_credential.get("target_scope"),
                )
        camera = CameraConfig.from_input(
            body.model_copy(update={"profile": domain, "enabled_events": enabled_events}),
            ref,
            camera_id=camera_id,
        )
        zone_warnings = service.event_zone_warnings(enabled_events, list(camera.zones))
        if zone_warnings:
            restore_update_credential()
            raise HTTPException(
                status_code=400,
                detail="영역 설정이 필요한 감지 항목이 있습니다: " + "; ".join(zone_warnings),
            )
        try:
            revision, _ = repo.save(
                camera,
                expected_revision if expected_revision is not None else observed_revision,
            )
        except RevisionConflict as exc:
            restore_update_credential()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if camera.enabled:
            applied, apply_error, pipeline_applied, gateway_synced = _apply_camera_runtime(
                camera,
                addresses,
            )
        else:
            applied, apply_error, pipeline_applied, gateway_synced = _remove_camera_runtime(camera_id)
        if not applied:
            try:
                rollback_revision, _ = repo.save(old, expected_revision=revision)
            except RevisionConflict as exc:
                service.write_audit(
                    "camera_update_rollback_conflict",
                    camera=camera_id,
                    apply_error=apply_error,
                )
                raise HTTPException(
                    status_code=409,
                    detail="새 설정 적용에 실패했고 다른 변경과 충돌해 자동 복구하지 못했습니다.",
                ) from exc
            restore_update_credential()
            if old.enabled:
                restored, restore_error, _, _ = _apply_camera_runtime(old)
            else:
                restored, restore_error, _, _ = _remove_camera_runtime(camera_id)
            service.write_audit(
                "camera_update_rolled_back",
                camera=camera_id,
                failed_revision=revision,
                rollback_revision=rollback_revision,
                restored=restored,
                apply_error=apply_error,
                restore_error=restore_error,
            )
            raise HTTPException(
                status_code=503,
                detail=(
                    "새 연결 설정을 실행 환경에 적용하지 못해 이전 설정으로 복구했습니다."
                    if restored else
                    "새 연결 설정 적용과 이전 설정 자동 복구가 모두 실패했습니다. 진단이 필요합니다."
                ),
            )
        if old.credential_ref and old.credential_ref != ref:
            # Only remove a legacy/stale reference after persistence and runtime handling.
            app.state.camera_credentials.delete(old.credential_ref)
        _invalidate_commissioning_acceptance()
        service.write_audit("camera_updated", camera=camera_id, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        return {
            "revision": revision,
            "camera": camera.public_dict(),
            "applied": applied,
            "apply_error": apply_error,
            "pipeline_applied": pipeline_applied,
            "gateway_synced": gateway_synced,
        }

    @app.delete("/api/cameras/{camera_id}", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def delete_camera(camera_id: str, expected_revision: int | None = Query(None, ge=0)) -> dict[str, Any]:
        repo = app.state.camera_repository
        current_revision, _ = repo.list()
        if expected_revision is not None and expected_revision != current_revision:
            raise HTTPException(
                status_code=409,
                detail=f"expected revision {expected_revision}, current revision {current_revision}",
            )
        old = repo.get(camera_id)
        if old is None:
            raise HTTPException(status_code=404, detail="camera not found")
        removed, apply_error, pipeline_removed, gateway_synced = _remove_camera_runtime(camera_id)
        if apply_error:
            # Keep the record and credential so an operator can retry cleanup;
            # otherwise an orphaned gateway path could remain viewable forever.
            service.write_audit(
                "camera_delete_cleanup_failed",
                camera=camera_id,
                pipeline_removed=pipeline_removed,
                gateway_synced=gateway_synced,
                apply_error=apply_error,
            )
            raise HTTPException(status_code=503, detail=f"camera cleanup failed: {apply_error}")
        try:
            revision, old = repo.delete(camera_id, expected_revision)
        except RevisionConflict as exc:
            _apply_camera_runtime(old)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="camera not found") from exc
        app.state.camera_credentials.delete(old.credential_ref)
        _invalidate_commissioning_acceptance()
        service.write_audit("camera_deleted", camera=camera_id, removed=removed,
                            pipeline_removed=pipeline_removed, gateway_synced=gateway_synced,
                            apply_error=apply_error)
        return {"revision": revision, "deleted": camera_id, "removed": removed,
                "apply_error": apply_error, "pipeline_removed": pipeline_removed,
                "gateway_synced": gateway_synced}

    @app.post("/api/cameras/{camera_id}/test", tags=["cameras"], dependencies=auth)
    def test_camera(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        addresses = _validate_camera_target(camera.rtsp_url)
        credential = app.state.camera_credentials.get(camera.credential_ref) or {}
        target_scope = CredentialStore.target_scope(
            addresses,
            urlsplit(camera.rtsp_url).port or 554,
        )
        stored_scope = credential.get("target_scope")
        if stored_scope and stored_scope != target_scope:
            raise HTTPException(
                status_code=409,
                detail="카메라 네트워크 주소가 변경되었습니다. 자격 증명을 다시 확인하세요.",
            )
        if credential and not stored_scope:
            raise HTTPException(
                status_code=409,
                detail="저장된 자격 증명의 장비 확인이 필요합니다. 자격 증명을 다시 입력하세요.",
            )
        return app.state.rtsp_probe.run(
            camera.rtsp_url,
            username=credential.get("username"),
            password=credential.get("password"),
            verify_stream=True,
            validated_addresses=addresses,
        )

    @app.post("/api/cameras/{camera_id}/test-draft", tags=["cameras"], dependencies=auth)
    def test_camera_draft(camera_id: str, body: CameraDraftProbeBody) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        addresses = _validate_camera_target(body.rtsp_url)
        username, password = _camera_credentials_for_target(camera, body, addresses)
        return app.state.rtsp_probe.run(
            body.rtsp_url,
            username=username,
            password=password,
            verify_stream=body.verify_stream,
            validated_addresses=addresses,
        )

    @app.post("/api/cameras/test", tags=["cameras"], dependencies=auth)
    def test_camera_transient(body: CameraProbeBody) -> dict[str, Any]:
        return app.state.rtsp_probe.run(
            body.rtsp_url,
            username=body.username,
            password=body.password,
            verify_stream=body.verify_stream,
        )

    @app.post("/api/cameras/{camera_id}/apply", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def apply_camera(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        _validate_camera_target(camera.rtsp_url)
        if not camera.enabled:
            removed, apply_error, pipeline_removed, gateway_synced = _remove_camera_runtime(camera_id)
            service.write_audit("camera_applied", camera=camera_id, applied=False,
                                pipeline_removed=pipeline_removed, gateway_synced=gateway_synced)
            return {"camera_id": camera_id, "applied": False, "removed": removed, "disabled": True,
                    "apply_error": apply_error, "pipeline_removed": pipeline_removed,
                    "gateway_synced": gateway_synced}
        applied, apply_error, pipeline_applied, gateway_synced = _apply_camera_runtime(camera)
        service.write_audit("camera_applied", camera=camera_id, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        if not applied:
            raise HTTPException(status_code=503, detail=f"camera apply failed: {apply_error}")
        return {"camera_id": camera_id, "applied": True, "pipeline_applied": pipeline_applied,
                "gateway_synced": gateway_synced}

    def _set_camera_enabled(
        camera_id: str,
        enabled: bool,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        repo = app.state.camera_repository
        observed_revision, cameras_at_read = repo.list()
        camera = next((item for item in cameras_at_read if item.id == camera_id), None)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        updated = camera.model_copy(update={"enabled": enabled})
        try:
            revision, _ = repo.save(
                updated,
                expected_revision=(
                    expected_revision if expected_revision is not None else observed_revision
                ),
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if enabled:
            applied, apply_error, pipeline_applied, gateway_synced = _apply_camera_runtime(updated)
        else:
            applied, apply_error, pipeline_applied, gateway_synced = _remove_camera_runtime(camera_id)
        if not applied:
            try:
                rollback_revision, _ = repo.save(camera, expected_revision=revision)
            except RevisionConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail="활성 상태 변경 실패 후 다른 설정과 충돌해 자동 복구하지 못했습니다.",
                ) from exc
            if camera.enabled:
                restored, restore_error, _, _ = _apply_camera_runtime(camera)
            else:
                restored, restore_error, _, _ = _remove_camera_runtime(camera_id)
            service.write_audit(
                "camera_enabled_rolled_back",
                camera=camera_id,
                requested_enabled=enabled,
                rollback_revision=rollback_revision,
                restored=restored,
                apply_error=apply_error,
                restore_error=restore_error,
            )
            raise HTTPException(
                status_code=503,
                detail="카메라 상태를 변경하지 못해 이전 상태로 복구했습니다.",
            )
        service.write_audit("camera_enabled_set", camera=camera_id, enabled=enabled, applied=applied,
                            pipeline_applied=pipeline_applied, gateway_synced=gateway_synced)
        _invalidate_commissioning_acceptance()
        return {
            "camera_id": camera_id,
            "enabled": enabled,
            "revision": revision,
            "applied": applied,
            "apply_error": apply_error,
            "pipeline_applied": pipeline_applied,
            "gateway_synced": gateway_synced,
        }

    @app.post("/api/cameras/{camera_id}/enable", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def enable_camera(
        camera_id: str,
        expected_revision: int | None = Query(None, ge=0),
    ) -> dict[str, Any]:
        return _set_camera_enabled(camera_id, True, expected_revision)

    @app.post("/api/cameras/{camera_id}/disable", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def disable_camera(
        camera_id: str,
        expected_revision: int | None = Query(None, ge=0),
    ) -> dict[str, Any]:
        return _set_camera_enabled(camera_id, False, expected_revision)

    @app.get("/api/cameras/readiness", tags=["cameras"], dependencies=auth)
    def cameras_readiness() -> dict[str, Any]:
        revision, configured = app.state.camera_repository.list()
        enabled = [c.id for c in configured if c.enabled]
        pipeline_obj = app.state.pipeline
        if pipeline_obj is None or not hasattr(pipeline_obj, "get_status"):
            return {"ready": bool(enabled), "revision": revision, "enabled_cameras": enabled}
        try:
            status = pipeline_obj.get_status() or {}
            telemetry = status.get("cameras", {}) if isinstance(status, dict) else {}
        except Exception:
            telemetry = {}

        def is_receiving(camera_id: str) -> bool:
            item = telemetry.get(camera_id)
            if not isinstance(item, dict):
                return False
            health_state = item.get("health_state")
            if health_state is not None:
                return health_state == "receiving"
            return bool(
                item.get("connected")
                and item.get("last_frame_time") is not None
                and not item.get("is_stale", True)
            )

        healthy = [
            camera_id for camera_id in enabled if is_receiving(camera_id)
        ]
        return {
            "ready": bool(enabled) and len(healthy) == len(enabled),
            "revision": revision,
            "enabled_cameras": enabled,
            "healthy_cameras": healthy,
        }

    @app.get("/api/cameras/health", tags=["cameras"], dependencies=auth)
    def cameras_health() -> dict[str, Any]:
        revision, configured = app.state.camera_repository.list()
        telemetry = {}
        if app.state.pipeline is not None and hasattr(app.state.pipeline, "get_status"):
            try:
                telemetry = (app.state.pipeline.get_status() or {}).get("cameras", {})
            except Exception:
                telemetry = {}
        return {
            "status": "ok",
            "revision": revision,
            "configured": len(configured),
            "enabled": sum(c.enabled for c in configured),
            "cameras": telemetry,
        }

    @app.get("/api/cameras/plan", tags=["cameras"], dependencies=auth)
    def cameras_plan() -> dict[str, Any]:
        revision, configured = app.state.camera_repository.list()
        return compile_plan(revision, configured).to_dict()

    @app.get("/api/cameras/telemetry", tags=["cameras"], dependencies=auth)
    def camera_telemetry() -> dict[str, Any]:
        generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        pl = app.state.pipeline
        if pl is None or not hasattr(pl, "get_status"):
            return {
                "connected": False,
                "telemetry_status": "unavailable",
                "generated_at": generated_at,
                "cameras": {},
            }
        try:
            status = pl.get_status() or {}
            if not isinstance(status, dict):
                raise TypeError("pipeline telemetry snapshot is not a mapping")
            cams = status.get("cameras", {})
            if not isinstance(cams, dict):
                raise TypeError("pipeline telemetry cameras is not a mapping")
        except Exception:
            # 기존 구현은 get_status 실패를 connected=true/cameras={}로 위장했다. 이는
            # 운영자에게 "카메라 없음"으로 보이므로 source-level error를 명시한다.
            return {
                "connected": False,
                "telemetry_status": "error",
                "generated_at": generated_at,
                "cameras": {},
            }
        return {
            "connected": True,
            "telemetry_status": "ok",
            "generated_at": generated_at,
            "cameras": cams,
        }

    @app.get("/api/cameras/{camera_id}", tags=["cameras"], dependencies=auth)
    def get_camera(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        revision, _ = app.state.camera_repository.list()
        return {"revision": revision, "camera": camera.public_dict()}

    @app.get(
        "/api/cameras/{camera_id}/tracking",
        tags=["cameras"],
        dependencies=auth,
    )
    def camera_tracking(camera_id: str) -> dict[str, Any]:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        if not camera.enabled:
            raise HTTPException(
                status_code=409,
                detail="disabled camera has no live tracking data",
            )
        pipeline_obj = app.state.pipeline
        get_snapshot = getattr(pipeline_obj, "get_tracking_snapshot", None)
        if not callable(get_snapshot):
            raise HTTPException(status_code=503, detail="tracking unavailable")
        return get_snapshot(camera_id)

    @app.put("/api/cameras/{camera_id}/zones", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def set_camera_zones(camera_id: str, body: CameraZonesBody) -> dict[str, Any]:
        repo = app.state.camera_repository
        observed_revision, cameras_at_read = repo.list()
        camera = next((item for item in cameras_at_read if item.id == camera_id), None)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        updated = camera.model_copy(update={"zones": body.zones})
        zone_warnings = service.event_zone_warnings(updated.enabled_events, updated.zones)
        if zone_warnings:
            raise HTTPException(
                status_code=400,
                detail="영역 기반 감지가 활성화되어 필요한 영역을 삭제하거나 변경할 수 없습니다: " + "; ".join(zone_warnings),
            )
        try:
            revision, _ = repo.save(
                updated,
                (
                    body.expected_revision
                    if body.expected_revision is not None
                    else observed_revision
                ),
            )
        except RevisionConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        applied, apply_error, pipeline_applied, gateway_synced = (
            _apply_camera_runtime(updated) if updated.enabled else (False, None, False, None)
        )
        if updated.enabled and not applied:
            try:
                rollback_revision, _ = repo.save(camera, expected_revision=revision)
            except RevisionConflict as exc:
                raise HTTPException(
                    status_code=409,
                    detail="영역 적용 실패 후 다른 설정과 충돌해 자동 복구하지 못했습니다.",
                ) from exc
            restored, restore_error, _, _ = _apply_camera_runtime(camera)
            service.write_audit(
                "camera_zones_rolled_back",
                camera=camera_id,
                rollback_revision=rollback_revision,
                restored=restored,
                apply_error=apply_error,
                restore_error=restore_error,
            )
            raise HTTPException(
                status_code=503,
                detail="새 영역을 실행 환경에 적용하지 못해 이전 영역으로 복구했습니다.",
            )
        service.write_audit("camera_zones_updated", camera=camera_id, zone_count=len(body.zones),
                            applied=applied, pipeline_applied=pipeline_applied,
                            gateway_synced=gateway_synced, apply_error=apply_error)
        _invalidate_commissioning_acceptance()
        return {
            "revision": revision, "camera_id": camera_id, "zones": [z.model_dump() for z in body.zones],
            "applied": applied, "apply_error": apply_error,
            "pipeline_applied": pipeline_applied, "gateway_synced": gateway_synced,
        }

    @app.get("/api/cameras/{camera_id}/snapshot", tags=["cameras"], dependencies=auth)
    def camera_snapshot(camera_id: str) -> Response:
        camera = app.state.camera_repository.get(camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail="camera not found")
        if not camera.enabled:
            raise HTTPException(status_code=409, detail="disabled camera has no live snapshot")
        pl = app.state.pipeline
        if pl is not None and hasattr(pl, "get_camera_snapshot"):
            image = pl.get_camera_snapshot(camera_id)
            if isinstance(image, bytes):
                return Response(
                    content=image,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"},
                )
        raise HTTPException(status_code=503, detail="live snapshot is unavailable without a mounted pipeline provider")

    @app.get("/api/onvif/discover", tags=["onvif"], dependencies=auth)
    def onvif_discover() -> dict[str, Any]:
        return app.state.onvif.discover()

    @app.post("/api/cameras/discover", tags=["onvif"], dependencies=auth)
    def camera_discover(body: CameraDiscoveryBody) -> dict[str, Any]:
        def discover_onvif() -> dict[str, Any]:
            result = app.state.onvif.discover()
            discovered = [
                {**device, "source_type": "onvif"}
                for device in result.get("devices", [])
                if isinstance(device, dict)
            ]
            devices = remember_onvif_devices(discovered)
            return {**result, "devices": devices}

        def discover_gateway() -> dict[str, Any]:
            gateway = app.state.mediamtx
            ready_paths = getattr(gateway, "ready_paths", None)
            if not callable(ready_paths):
                return {"available": False, "devices": [], "reason": "영상 Gateway 검색을 사용할 수 없습니다."}
            try:
                paths = ready_paths()
            except MediaMTXError:
                return {"available": False, "devices": [], "reason": "영상 Gateway에 연결할 수 없습니다."}
            existing_ids = {camera.id for camera in app.state.camera_repository.list()[1]}
            rtsp_base = os.environ.get("KSI_CCTV_GATEWAY_RTSP_URL", "rtsp://mediamtx:8554").rstrip("/")
            devices = []
            for path in paths:
                if path["name"] in existing_ids:
                    continue
                video: dict[str, Any] = next((track for track in path.get("tracks", []) if track.get("codec")), {})
                dimensions = (
                    f"{video.get('width')}x{video.get('height')}"
                    if video.get("width") and video.get("height") else "해상도 확인 필요"
                )
                devices.append({
                    "name": f"연결된 영상 {path['name']}",
                    "host": "영상 Gateway",
                    "rtsp_url": f"{rtsp_base}/{path['name']}",
                    "model": f"{video.get('codec') or '영상'} · {dimensions}",
                    "source_type": "gateway",
                })
            return {"available": True, "devices": devices}

        if body.protocol == "onvif":
            return discover_onvif()
        if body.protocol == "gateway":
            return discover_gateway()

        onvif = discover_onvif()
        gateway = discover_gateway()
        devices = [*onvif["devices"], *gateway["devices"]]
        available = bool(onvif.get("available") or gateway.get("available"))
        reason = None if available else "; ".join(
            str(source["reason"])
            for source in (onvif, gateway)
            if source.get("reason")
        ) or "카메라 검색을 사용할 수 없습니다."
        return {
            "available": available,
            "devices": devices,
            "sources": {
                "onvif": {"available": bool(onvif.get("available")), "reason": onvif.get("reason")},
                "gateway": {"available": bool(gateway.get("available")), "reason": gateway.get("reason")},
            },
            **({"reason": reason} if reason else {}),
        }

    @app.get("/api/onvif/profiles", tags=["onvif"], dependencies=auth)
    def onvif_profiles(discovery_id: str = Query(..., min_length=16, max_length=128)) -> dict[str, Any]:
        host, port = discovered_onvif_target(discovery_id)
        try:
            addresses = app.state.rtsp_probe.allowed_host(host, port)
            if not addresses:
                raise HTTPException(status_code=400, detail="ONVIF host must resolve only to private LAN addresses")
        except OSError as exc:
            raise HTTPException(status_code=400, detail="ONVIF host could not be resolved") from exc
        return app.state.onvif.profiles(addresses[0], port)

    @app.post("/api/onvif/profiles", tags=["onvif"], dependencies=auth)
    def onvif_profiles_authenticated(body: OnvifProfilesBody) -> dict[str, Any]:
        host, port = discovered_onvif_target(body.discovery_id)
        try:
            addresses = app.state.rtsp_probe.allowed_host(host, port)
            if not addresses:
                raise HTTPException(status_code=400, detail="ONVIF host must resolve only to private LAN addresses")
        except OSError as exc:
            raise HTTPException(status_code=400, detail="ONVIF host could not be resolved") from exc
        return app.state.onvif.profiles(
            addresses[0],
            port,
            body.username,
            body.password,
        )

    # ── 카메라 탐지 프로파일(도메인·이벤트 편집) ─────────────────────────
    @app.get("/api/camera-profiles", tags=["cameras"], dependencies=auth)
    def camera_profiles() -> dict[str, Any]:
        return service.get_camera_profiles()

    @app.put("/api/cameras/{camera_id}/profile", tags=["cameras"], dependencies=auth)
    @_serialized_camera_mutation
    def set_camera_profile(camera_id: str, body: CameraProfileBody) -> dict[str, Any]:
        try:
            observed_revision, managed_cameras = app.state.camera_repository.list()
            managed = next((item for item in managed_cameras if item.id == camera_id), None)
            if managed is not None:
                enabled_events = service.validate_event_selection(
                    body.domain, body.enabled_events
                )
                zone_warnings = service.event_zone_warnings(
                    enabled_events, list(managed.zones)
                )
                if zone_warnings:
                    raise ValueError(
                        "영역 설정이 필요한 감지 항목이 있습니다: "
                        + "; ".join(zone_warnings)
                    )
                revision, updated = app.state.camera_repository.update_profile(
                    camera_id,
                    body.domain,
                    enabled_events,
                    (
                        body.expected_revision
                        if body.expected_revision is not None
                        else observed_revision
                    ),
                )
                applied, apply_error, pipeline_applied, gateway_synced = (
                    _apply_camera_runtime(updated) if updated and updated.enabled else (False, None, False, None)
                )
                if updated.enabled and not applied:
                    try:
                        rollback_revision, _ = app.state.camera_repository.save(
                            managed,
                            expected_revision=revision,
                        )
                    except RevisionConflict as exc:
                        raise HTTPException(
                            status_code=409,
                            detail="감지 설정 적용 실패 후 다른 설정과 충돌해 자동 복구하지 못했습니다.",
                        ) from exc
                    restored, restore_error, _, _ = _apply_camera_runtime(managed)
                    service.write_audit(
                        "camera_profile_rolled_back",
                        camera=camera_id,
                        rollback_revision=rollback_revision,
                        restored=restored,
                        apply_error=apply_error,
                        restore_error=restore_error,
                    )
                    raise HTTPException(
                        status_code=503,
                        detail="새 감지 설정을 실행 환경에 적용하지 못해 이전 설정으로 복구했습니다.",
                    )
                result = {
                    "camera_id": camera_id,
                    "domain": body.domain,
                    "enabled_events": enabled_events,
                    "revision": revision,
                    "applied": applied,
                    "apply_error": apply_error,
                    "pipeline_applied": pipeline_applied,
                    "gateway_synced": gateway_synced,
                    "configuration_warnings": service.event_zone_warnings(
                        enabled_events, list(updated.zones)
                    ),
                }
                _invalidate_commissioning_acceptance()
            else:
                result = service.set_camera_profile(camera_id, body.domain, body.enabled_events)
                pipeline = app.state.pipeline
                manager = getattr(pipeline, "camera_manager", None)
                mounted = (
                    manager.get_camera(camera_id)
                    if manager is not None and hasattr(manager, "get_camera")
                    else None
                )
                if mounted is not None and hasattr(pipeline, "set_camera_profile"):
                    current_profiles = getattr(pipeline, "_camera_profiles", {})
                    current = current_profiles.get(camera_id, {}) if isinstance(current_profiles, dict) else {}
                    runtime_profile = {
                        **(current if isinstance(current, dict) else {}),
                        "domain": body.domain,
                        "enabled_events": list(result["enabled_events"]),
                        "events": list(result["enabled_events"]),
                    }
                    pipeline.set_camera_profile(camera_id, runtime_profile)
                    result["applied"] = True
                    result["pipeline_applied"] = True
            service.write_audit(
                "camera_profile_set",
                camera=camera_id,
                domain=body.domain,
                event_count=len(result["enabled_events"]),
                applied=result.get("applied"),
                pipeline_applied=result.get("pipeline_applied"),
                gateway_synced=result.get("gateway_synced"),
                apply_error=result.get("apply_error"),
            )
            return result
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except RevisionConflict as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        except KeyError as e:
            raise HTTPException(status_code=404, detail="camera not found") from e
