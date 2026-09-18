"""이벤트/인시던트/관측/SSE/WS 라우트. 경로·동작 무변경 이동."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from src.api import service
from src.api.actor import current_actor
from src.api.auth import check_api_key


class IncidentActionBody(BaseModel):
    """Single-admin incident action; AI event lifecycle remains immutable."""

    status: Literal["acknowledged", "in_progress", "resolved"]
    disposition: Literal["confirmed", "false_positive", "duplicate", "test"] | None = None
    note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def resolved_requires_disposition(self) -> IncidentActionBody:
        if self.status == "resolved" and self.disposition is None:
            raise ValueError("resolved incidents require a disposition")
        return self


def register(app, auth) -> None:
    @app.get("/api/events", tags=["events"], dependencies=auth)
    def events(
        limit: int = Query(100, ge=1, le=service.EVENTS_MAX_LIMIT),
        event_type: str | None = Query(None),
        camera_id: str | None = Query(None),
        alert_level: str | None = Query(None),
        since: str | None = Query(None),
        until: str | None = Query(None),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        items = service.list_events(
            limit=limit,
            event_type=event_type,
            camera_id=camera_id,
            alert_level=alert_level,
            since=since,
            until=until,
            offset=offset,
            event_repository=app.state.event_repository,
            durable_store_status=app.state.durable_event_store_status,
        )
        return {"count": len(items), "events": items}

    @app.get("/api/incidents", tags=["incidents"], dependencies=auth)
    def incidents(
        status: Literal["new", "acknowledged", "in_progress", "resolved"] | None = Query(None),
        category: Literal["safety", "camera_health"] | None = Query(None),
        unresolved: bool = Query(False),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, Any]:
        if status is not None and unresolved:
            raise HTTPException(status_code=400, detail="status와 unresolved는 함께 사용할 수 없습니다.")
        store = app.state.incident_store
        if store is None:
            return {"available": False, "count": 0, "incidents": []}
        items = store.list(status=status, category=category, unresolved=unresolved, limit=limit)
        missing_alert_levels = [
            str(item["event_id"])
            for item in items
            if item.get("source_event_alert_level") is None and item.get("event_id")
        ]
        event_repository = app.state.event_repository
        if missing_alert_levels and hasattr(event_repository, "get_alert_levels"):
            try:
                alert_levels = event_repository.get_alert_levels(missing_alert_levels)
                if app.state.durable_event_store_status is not None:
                    app.state.durable_event_store_status.record_success(operation="query:alert_levels")
            except Exception as error:
                if app.state.durable_event_store_status is not None:
                    app.state.durable_event_store_status.record_failure(error, operation="query:alert_levels")
                alert_levels = {}
            if alert_levels:
                store.backfill_source_alert_levels(alert_levels)
                for item in items:
                    if item.get("source_event_alert_level") is None:
                        item["source_event_alert_level"] = alert_levels.get(str(item["event_id"]))
        return {"available": True, "count": len(items), "incidents": items}

    @app.post("/api/incidents/{event_id}/actions", tags=["incidents"], dependencies=auth)
    def incident_action(event_id: str, body: IncidentActionBody) -> dict[str, Any]:
        if not event_id or len(event_id) > 128 or any(character in event_id for character in "\r\n"):
            raise HTTPException(status_code=400, detail="유효하지 않은 이벤트 ID입니다.")
        store = app.state.incident_store
        if store is None:
            raise HTTPException(status_code=503, detail="사건 저장소가 연결되지 않았습니다.")
        try:
            event = service.get_event(
                event_id,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            )
        except service.EventLookupUnavailable as error:
            raise HTTPException(status_code=503, detail="이벤트 저장소를 조회할 수 없습니다.") from error
        if event is None:
            raise HTTPException(status_code=404, detail="이벤트 증거를 찾을 수 없습니다.")
        try:
            incident = store.act(
                event_id,
                status=body.status,
                disposition=body.disposition,
                note=body.note.strip(),
                actor=current_actor(),
            )
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        # Notes can contain sensitive operational context, so the signed admin audit
        # only records the state transition and durable event reference.
        service.write_audit(
            "incident_action",
            incident_event_id=event_id,
            incident_status=body.status,
            incident_disposition=body.disposition,
        )
        return incident

    @app.get("/api/incidents/{event_id}", tags=["incidents"], dependencies=auth)
    def incident_detail(event_id: str) -> dict[str, Any]:
        store = app.state.incident_store
        if store is None:
            raise HTTPException(status_code=503, detail="사건 저장소가 연결되지 않았습니다.")
        incident = store.get(event_id)
        if incident is None:
            raise HTTPException(status_code=404, detail="사건 처리 기록이 없습니다.")
        return {"incident": incident, "actions": store.actions(event_id)}

    @app.get("/api/observations", tags=["observations"], dependencies=auth)
    def observations(
        camera_id: str | None = Query(None, max_length=64),
        limit: int = Query(120, ge=1, le=2000),
    ) -> dict[str, Any]:
        pipeline = app.state.pipeline
        if pipeline is None or not hasattr(pipeline, "get_observations"):
            return {"connected": False, "count": 0, "observations": []}
        items = pipeline.get_observations(camera_id=camera_id, limit=limit)
        return {"connected": True, "count": len(items), "observations": items}

    # ── 이벤트 실시간 스트림(SSE) — durable SQLite cursor 우선 ───────────────
    @app.get("/api/events/stream", tags=["events"], dependencies=auth)
    async def events_stream(request: Request) -> StreamingResponse:
        # EventSource 표준 재연결 cursor. SQLite가 있으면 monotonic stream_id를 사용하고,
        # legacy deployments only fall back to alert-log event IDs.
        last_event_id = request.headers.get("last-event-id")
        return StreamingResponse(
            service.stream_events(
                request.is_disconnected,
                last_event_id=last_event_id,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # nginx 등 역프록시 버퍼링 비활성(즉시 전달)
            },
        )

    # Register the literal stream path first so it cannot be interpreted as an event ID.
    @app.get("/api/events/{event_id}", tags=["events"], dependencies=auth)
    def event_detail(event_id: str) -> dict[str, Any]:
        if not event_id or len(event_id) > 128 or any(character in event_id for character in "\r\n"):
            raise HTTPException(status_code=400, detail="유효하지 않은 이벤트 ID입니다.")
        try:
            event = service.get_event(
                event_id,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            )
        except service.EventLookupUnavailable as error:
            raise HTTPException(status_code=503, detail="이벤트 저장소를 조회할 수 없습니다.") from error
        if event is None:
            raise HTTPException(status_code=404, detail="이벤트 증거를 찾을 수 없습니다.")
        return {"event": event}

    # ── 이벤트 실시간 스트림(WebSocket) — 양방향, 향후 프레임 푸시 확장 ─────
    @app.websocket("/ws/events")
    async def events_ws(websocket: WebSocket):
        """이벤트 실시간 WebSocket 스트림.

        SSE와 동일하게 durable SQLite(legacy에서는 alerts.log)의 신규 이벤트를 push. 연결 시 즉시 최근 이벤트 목록을
        전송하고, 이후 신규 이벤트를 JSON 프레임으로 push. 클라이언트가 ping을내면
        pong으로 응답(keepalive). WS는 Depends 사용이 제한적이므로 핸드셰이크의
        X-API-Key 헤더를 수동 검증한다. 브라우저 클라이언트는 인증된 SSE를 사용한다.
        """

        def _is_disconnected() -> bool:
            return websocket.client_state.name == "DISCONNECTED"

        async def _is_disconnected_async() -> bool:
            return _is_disconnected()

        api_key = websocket.headers.get("x-api-key")
        if not check_api_key(api_key):
            await websocket.close(code=4401, reason="인증 필요")
            return

        await websocket.accept()

        requested_cursor = websocket.headers.get("last-event-id") or websocket.query_params.get("cursor")
        cursor: int | None = None
        reconciliation_reason: str | None = None
        if app.state.event_repository is not None:
            try:
                # Snapshot before list_events closes the initial replay/subscription gap.
                cursor, reconciliation_reason = service.prepare_durable_event_cursor(
                    app.state.event_repository,
                    requested_cursor,
                    app.state.durable_event_store_status,
                )
            except Exception:
                reconciliation_reason = "durable-store-unavailable"

        # 연결 즉시 최근 이벤트 20건 전송(초기 로드)
        try:
            # A reconnect with a valid durable cursor must replay transitions in order. Sending
            # the current snapshot first can otherwise overwrite confirmed state with a replayed
            # provisional record that has the same event_id.
            recent = [] if requested_cursor and reconciliation_reason is None else service.list_events(
                limit=20,
                event_repository=app.state.event_repository,
                durable_store_status=app.state.durable_event_store_status,
            )
            initial: dict[str, Any] = {"type": "initial", "events": recent}
            if cursor is not None:
                initial["cursor"] = cursor
            await websocket.send_json(initial)

            # 이후 durable cursor로 신규 이벤트 push
            async for frame in service.stream_events_ws(
                _is_disconnected_async,
                event_repository=app.state.event_repository,
                cursor=cursor,
                reconciliation_reason=reconciliation_reason,
                durable_store_status=app.state.durable_event_store_status,
            ):
                if _is_disconnected():
                    break
                # ping/pong keepalive
                if frame.get("type") == "heartbeat" and not _is_disconnected():
                    await websocket.send_json({"type": "pong"})
                else:
                    await websocket.send_json(frame)
        except WebSocketDisconnect:
            return
        except Exception:
            return
