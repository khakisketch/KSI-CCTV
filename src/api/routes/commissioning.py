"""커미셔닝 그룹 라우트. 경로·태그·상태코드·응답 형태 무변경 이동."""
from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from io import BytesIO
from typing import Any

from fastapi import HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from src.api import service
from src.commissioning import CommissioningConflict


class CommissioningSiteBody(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    location: str = Field(default="", max_length=256)
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=64)
    installer: str = Field(default="", max_length=128)
    expected_camera_count: int = Field(default=0, ge=0, le=16)
    expected_revision: int | None = Field(default=None, ge=0)


class CameraCommissioningBody(BaseModel):
    live_verified: bool = False
    reconnect_verified: bool = False
    detection_verified: bool = False
    alert_verified: bool = False
    notes: str = Field(default="", max_length=1000)
    expected_revision: int | None = Field(default=None, ge=0)


class CommissioningRevisionBody(BaseModel):
    expected_revision: int | None = Field(default=None, ge=0)


class HandoffAcceptanceBody(BaseModel):
    customer_name: str = Field(min_length=1, max_length=128)
    notes: str = Field(default="", max_length=1000)
    expected_revision: int | None = Field(default=None, ge=0)


def register(
    app,
    auth,
    *,
    build_checker: Callable[[], Any],
    runtime_event_catalog: Callable[[], dict[str, Any]],
) -> None:
    def _commissioning_snapshot() -> dict[str, Any]:
        state = app.state.commissioning.read()
        _, managed = app.state.camera_repository.list()
        pipeline_obj = app.state.pipeline
        try:
            pipeline_status = (
                pipeline_obj.get_status()
                if pipeline_obj is not None and hasattr(pipeline_obj, "get_status")
                else {}
            ) or {}
        except Exception:
            pipeline_status = {}
        telemetry = pipeline_status.get("cameras", {})
        if not isinstance(telemetry, dict):
            telemetry = {}
        health = build_checker().run_checks().to_dict()
        event_catalog = runtime_event_catalog()
        catalog_by_name = {
            item["name"]: item
            for item in event_catalog.get("catalog", [])
            if isinstance(item, dict) and item.get("name")
        }

        checks: list[dict[str, Any]] = []

        def add_check(
            check_id: str,
            label: str,
            status: str,
            summary: str,
            remediation: str,
        ) -> None:
            checks.append({
                "id": check_id,
                "label": label,
                "status": status,
                "summary": summary,
                "remediation": remediation,
            })

        site = state["site"]
        site_ready = bool(str(site.get("name", "")).strip())
        add_check(
            "site",
            "현장 정보",
            "pass" if site_ready else "fail",
            f"현장: {site['name']}" if site_ready else "현장 이름이 아직 설정되지 않았습니다.",
            "설치 진행에서 현장 이름과 예상 카메라 대수를 저장하세요.",
        )
        add_check(
            "engine",
            "관리 서비스",
            "pass",
            "관리 API가 정상 응답 중입니다.",
            "문제가 반복되면 진단 화면에서 서비스 상태를 다시 확인하세요.",
        )
        gateway_ready = app.state.mediamtx is not None
        add_check(
            "gateway",
            "영상 서비스",
            "pass" if gateway_ready else "fail",
            "영상 Gateway가 구성되어 있습니다." if gateway_ready else "영상 Gateway가 구성되지 않았습니다.",
            "Appliance 서비스 상태와 KSI_CCTV_MEDIAMTX_API_URL 설정을 확인하세요.",
        )
        pipeline_running = bool(pipeline_status.get("running"))
        add_check(
            "pipeline",
            "분석 서비스",
            "pass" if pipeline_running else "fail",
            "분석 Pipeline이 실행 중입니다." if pipeline_running else "분석 Pipeline이 중지되었거나 연결되지 않았습니다.",
            "대시보드에서 분석 서비스를 시작한 뒤 다시 검사하세요.",
        )
        system_ready = health.get("status") == "healthy"
        add_check(
            "system",
            "장비 자원",
            "pass" if system_ready else "fail",
            "메모리와 저장공간이 정상 범위입니다." if system_ready else "하나 이상의 장비 상태 검사가 실패했습니다.",
            "진단 화면에서 실패한 항목의 사용률과 서비스 메시지를 확인하세요.",
        )

        expected = int(site.get("expected_camera_count") or 0)
        camera_count_ok = bool(managed) and (expected == 0 or len(managed) >= expected)
        if camera_count_ok:
            camera_summary = f"관리형 카메라 {len(managed)}대가 등록되었습니다."
        elif expected > len(managed):
            camera_summary = f"예상 {expected}대 중 {len(managed)}대만 등록되었습니다."
        else:
            camera_summary = "등록된 관리형 카메라가 없습니다."
        add_check(
            "inventory",
            "카메라 목록",
            "pass" if camera_count_ok else "fail",
            camera_summary,
            "카메라 메뉴에서 누락된 카메라를 검색하거나 직접 등록하세요.",
        )

        camera_rows: list[dict[str, Any]] = []
        zone_issues: list[str] = []
        runtime_issues: list[str] = []
        acceptance_issues: list[str] = []
        for camera in managed:
            status = telemetry.get(camera.id, {})
            if not isinstance(status, dict):
                status = {}
            receiving = status.get("health_state") == "receiving"
            zone_warnings = service.event_zone_warnings(
                list(camera.enabled_events), list(camera.zones)
            )
            if zone_warnings:
                zone_issues.append(camera.name)
            ai_events = [
                name
                for name in camera.enabled_events
                if catalog_by_name.get(name, {}).get("runtime") in {"detection", "tracking"}
            ]
            ai_available = bool(event_catalog.get("runtime", {}).get("ai_available"))
            if ai_events and not ai_available:
                runtime_issues.append(camera.name)
            saved_checks = state["camera_checks"].get(camera.id, {})
            acceptance_ready = bool(
                saved_checks.get("live_verified")
                and saved_checks.get("reconnect_verified")
                and (not ai_events or saved_checks.get("detection_verified"))
                and (not camera.enabled_events or saved_checks.get("alert_verified"))
            )
            if not acceptance_ready:
                acceptance_issues.append(camera.name)
            camera_ready = bool(
                camera.enabled
                and receiving
                and not zone_warnings
                and not (ai_events and not ai_available)
                and acceptance_ready
            )
            camera_rows.append({
                "id": camera.id,
                "name": camera.name,
                "location": camera.location,
                "enabled": camera.enabled,
                "health_state": status.get("health_state", "unavailable"),
                "fps": status.get("fps"),
                "dropped_frames": status.get("dropped_frames"),
                "seconds_since_last_frame": status.get("seconds_since_last_frame"),
                "reconnect_attempts": status.get("reconnect_attempts"),
                "consecutive_failures": status.get("consecutive_failures"),
                "enabled_events": list(camera.enabled_events),
                "zone_count": len(camera.zones),
                "zone_warnings": zone_warnings,
                "ai_events": ai_events,
                "checks": saved_checks,
                "ready": camera_ready,
            })

        add_check(
            "camera_runtime",
            "카메라 영상 수신",
            "pass" if managed and all(row["health_state"] == "receiving" for row in camera_rows) else "fail",
            "모든 카메라가 정상 수신 중입니다." if managed and all(row["health_state"] == "receiving" for row in camera_rows) else "수신 상태를 확인해야 하는 카메라가 있습니다.",
            "카메라별 연결 검사와 최근 프레임 시간을 확인하세요.",
        )
        add_check(
            "zones",
            "필수 영역",
            "pass" if not zone_issues else "fail",
            "선택한 감지 항목에 필요한 영역이 준비되었습니다." if not zone_issues else f"영역 설정 필요: {', '.join(zone_issues)}",
            "카메라 상세에서 제한구역, 배회구역 또는 통과선을 먼저 설정하세요.",
        )
        add_check(
            "ai_runtime",
            "AI 실행 준비",
            "pass" if not runtime_issues else "fail",
            "선택한 감지 항목을 실행할 수 있습니다." if not runtime_issues else f"활성 AI 모델 없음: {', '.join(runtime_issues)}",
            "관리자 모델 화면에서 필요한 모델을 설치·활성화하고 추론 상태를 확인하세요.",
        )
        add_check(
            "acceptance",
            "카메라 인수시험",
            "pass" if managed and not acceptance_issues else "fail",
            "모든 카메라 인수시험이 기록되었습니다." if managed and not acceptance_issues else f"시험 미완료: {', '.join(acceptance_issues) if acceptance_issues else '등록 카메라 없음'}",
            "설치 진행에서 라이브, 재연결, 탐지, 알림 시험 결과를 기록하세요.",
        )

        blocking = [check for check in checks if check["status"] == "fail"]
        passed = sum(check["status"] == "pass" for check in checks)
        return {
            "schema_version": 1,
            "revision": state["revision"],
            "site": site,
            "completed_at": state.get("completed_at"),
            "handoff": state.get("handoff", {}),
            "updated_at": state.get("updated_at"),
            "ready_to_complete": len(blocking) == 0,
            "progress": {
                "passed": passed,
                "total": len(checks),
                "percent": round((passed / len(checks)) * 100) if checks else 0,
            },
            "checks": checks,
            "cameras": camera_rows,
            "health": health,
            "event_runtime": event_catalog.get("runtime", {}),
        }

    @app.get("/api/video-sources/capabilities", tags=["commissioning"], dependencies=auth)
    def video_source_capabilities() -> dict[str, Any]:
        return {
            "adapters": [
                {
                    "kind": "onvif",
                    "label": "ONVIF 네트워크 카메라",
                    "available": True,
                    "discovery": True,
                    "description": "같은 CCTV 네트워크에서 장비와 영상 프로필을 검색합니다.",
                },
                {
                    "kind": "rtsp",
                    "label": "RTSP 카메라",
                    "available": True,
                    "discovery": False,
                    "description": "카메라 또는 NVR 채널의 RTSP 주소를 직접 등록합니다.",
                },
                {
                    "kind": "gateway",
                    "label": "연결된 영상 Gateway",
                    "available": app.state.mediamtx is not None,
                    "discovery": app.state.mediamtx is not None,
                    "description": "Appliance 영상 Gateway에 이미 게시된 스트림을 사용합니다.",
                },
                {
                    "kind": "v4l2",
                    "label": "로컬 USB 카메라",
                    "available": False,
                    "discovery": False,
                    "description": "현재 버전은 관리형 RTSP bridge를 통해 등록합니다.",
                    "reason": "USB 장치 lifecycle adapter는 후속 하드웨어 검증이 필요합니다.",
                },
            ]
        }

    @app.get("/api/preflight", tags=["commissioning"], dependencies=auth)
    def preflight() -> dict[str, Any]:
        snapshot = _commissioning_snapshot()
        return {
            "ready": snapshot["ready_to_complete"],
            "progress": snapshot["progress"],
            "checks": snapshot["checks"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/commissioning", tags=["commissioning"], dependencies=auth)
    def commissioning_status() -> dict[str, Any]:
        return _commissioning_snapshot()

    @app.put("/api/commissioning/site", tags=["commissioning"], dependencies=auth)
    def commissioning_site(body: CommissioningSiteBody) -> dict[str, Any]:
        try:
            app.state.commissioning.update_site(
                body.model_dump(exclude={"expected_revision"}),
                body.expected_revision,
            )
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_site_updated", expected_camera_count=body.expected_camera_count)
        return _commissioning_snapshot()

    @app.put(
        "/api/commissioning/cameras/{camera_id}",
        tags=["commissioning"],
        dependencies=auth,
    )
    def commissioning_camera(camera_id: str, body: CameraCommissioningBody) -> dict[str, Any]:
        if app.state.camera_repository.get(camera_id) is None:
            raise HTTPException(status_code=404, detail="camera not found")
        try:
            app.state.commissioning.update_camera(
                camera_id,
                body.model_dump(exclude={"expected_revision"}),
                body.expected_revision,
            )
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("camera_commissioning_updated", camera=camera_id)
        return _commissioning_snapshot()

    @app.post("/api/commissioning/complete", tags=["commissioning"], dependencies=auth)
    def commissioning_complete(body: CommissioningRevisionBody) -> dict[str, Any]:
        snapshot = _commissioning_snapshot()
        if not snapshot["ready_to_complete"]:
            failed = [check["label"] for check in snapshot["checks"] if check["status"] == "fail"]
            raise HTTPException(
                status_code=409,
                detail="완료되지 않은 필수 검사: " + ", ".join(failed),
            )
        try:
            app.state.commissioning.mark_completed(body.expected_revision)
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_completed")
        return _commissioning_snapshot()

    @app.post("/api/commissioning/reopen", tags=["commissioning"], dependencies=auth)
    def commissioning_reopen(body: CommissioningRevisionBody) -> dict[str, Any]:
        try:
            app.state.commissioning.reopen(body.expected_revision)
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_reopened")
        return _commissioning_snapshot()

    @app.put("/api/commissioning/handoff", tags=["commissioning"], dependencies=auth)
    def commissioning_handoff(body: HandoffAcceptanceBody) -> dict[str, Any]:
        try:
            app.state.commissioning.record_handoff(
                body.customer_name,
                body.notes,
                body.expected_revision,
            )
        except CommissioningConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        service.write_audit("commissioning_handoff_accepted")
        return _commissioning_snapshot()

    @app.get("/api/commissioning/report", tags=["commissioning"], dependencies=auth)
    def commissioning_report(response: Response) -> dict[str, Any]:
        response.headers["Cache-Control"] = "private, no-store"
        snapshot = _commissioning_snapshot()
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "product": "KSI-CCTV",
            "site": snapshot["site"],
            "revision": snapshot["revision"],
            "completed_at": snapshot["completed_at"],
            "handoff": snapshot["handoff"],
            "ready": snapshot["ready_to_complete"],
            "progress": snapshot["progress"],
            "checks": snapshot["checks"],
            "cameras": snapshot["cameras"],
        }

    @app.get("/api/support/bundle", tags=["commissioning"], dependencies=auth)
    def support_bundle() -> Response:
        """Download a redacted diagnostic bundle without credentials or source URLs."""
        snapshot = _commissioning_snapshot()
        generated_at = datetime.now(timezone.utc).isoformat()
        health = snapshot["health"]
        system = health.get("system") if isinstance(health, dict) else {}
        safe_system = {
            name: system[name]
            for name in (
                "cpu_percent",
                "memory_percent",
                "memory_used_mb",
                "memory_total_mb",
                "disk_percent",
                "disk_used_gb",
                "disk_total_gb",
                "gpu_percent",
                "gpu_memory_percent",
                "gpu_temperature",
            )
            if isinstance(system, dict) and isinstance(system.get(name), (int, float))
        }
        components = health.get("components") if isinstance(health, dict) else []
        safe_health = {
            "status": health.get("status") if isinstance(health, dict) and health.get("status") in {"healthy", "unhealthy"} else "unknown",
            "system": safe_system,
            "component_count": len(components) if isinstance(components, list) else 0,
            "unhealthy_component_count": sum(
                1 for component in components
                if isinstance(component, dict) and component.get("healthy") is False
            ) if isinstance(components, list) else 0,
            "ai_runtime": snapshot["event_runtime"].get("ai_runtime", {}),
        }
        commissioning = {
            # Site identity and location belong to the commissioning report, not a portable support archive.
            "site_configured": bool(snapshot["site"]),
            "completed_at": snapshot["completed_at"],
            "progress": snapshot["progress"],
            # Check summaries and camera names/locations can contain site-specific information.
            "checks": [
                {"id": check["id"], "status": check["status"]}
                for check in snapshot["checks"]
            ],
            "cameras": [
                {
                    "enabled": camera["enabled"],
                    "health_state": camera["health_state"],
                    "fps": camera["fps"],
                    "dropped_frames": camera["dropped_frames"],
                    "seconds_since_last_frame": camera["seconds_since_last_frame"],
                    "reconnect_attempts": camera["reconnect_attempts"],
                    "consecutive_failures": camera["consecutive_failures"],
                    "enabled_events": camera["enabled_events"],
                    "zone_count": camera["zone_count"],
                    "ready": camera["ready"],
                }
                for camera in snapshot["cameras"]
            ],
            "event_runtime": snapshot["event_runtime"],
        }
        audit_entries, audit_anchor = service.list_audit_window(limit=200)
        files = {
            "commissioning.json": commissioning,
            "health.json": safe_health,
            # Bounded evidence remains independently verifiable after audit log retention advances.
            "audit.json": {"entries": audit_entries, "anchor_previous_hash": audit_anchor},
        }
        encoded_files = {
            name: json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
            for name, data in files.items()
        }
        manifest = {
            "schema_version": 2,
            "product": "KSI-CCTV",
            "generated_at": generated_at,
            "files": {
                name: hashlib.sha256(data).hexdigest()
                for name, data in encoded_files.items()
            },
            "audit_public_key": service.audit_public_key(),
            "redacted": True,
        }
        manifest["signature"] = service.sign_audit_data(service.canonical_json_bytes(manifest))
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            for name, data in encoded_files.items():
                archive.writestr(name, data)
        service.write_audit("support_bundle_created")
        return Response(
            content=payload.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": "attachment; filename=ksi-cctv-support.zip",
                "Cache-Control": "no-store",
            },
        )
