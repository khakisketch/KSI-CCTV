"""Small, dependency-free client for MediaMTX's v3 path configuration API."""
from __future__ import annotations

import json
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_CAMERA_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class MediaMTXError(RuntimeError):
    """Sanitized gateway error; never includes endpoint or source details."""

    def __init__(self, operation: str, status: int | None = None):
        self.operation = operation
        self.status = status
        super().__init__(f"{operation}_{status}" if status is not None else operation)


class MediaMTXClient:
    """Manage installer camera paths without exposing MediaMTX configuration."""

    def __init__(self, api_url: str, timeout: float = 3.0):
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, endpoint: str, payload: dict | None = None) -> int:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f"{self.api_url}/v3/config/paths/{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"} if data is not None else {},
            method=method,
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured local gateway
                return response.status
        except HTTPError as exc:
            return exc.code
        except (URLError, OSError, ValueError) as exc:
            raise MediaMTXError("request_failed") from exc

    @staticmethod
    def _path(camera_id: str) -> str:
        if not _CAMERA_ID.fullmatch(camera_id):
            raise MediaMTXError("invalid_camera_id")
        return camera_id

    def sync(self, camera_id: str, source: str) -> None:
        path = self._path(camera_id)
        payload = {"source": source, "sourceOnDemand": True, "rtspTransport": "tcp"}
        status = self._request("POST", f"add/{path}", payload)
        if 200 <= status < 300:
            return
        if status not in {400, 409}:
            raise MediaMTXError("add_failed", status)
        status = self._request("PATCH", f"patch/{path}", payload)
        if not 200 <= status < 300:
            raise MediaMTXError("patch_failed", status)

    def delete(self, camera_id: str) -> None:
        path = self._path(camera_id)
        status = self._request("DELETE", f"delete/{path}")
        if status == 404 or 200 <= status < 300:
            return
        raise MediaMTXError("delete_failed", status)

    def ready_paths(self) -> list[dict]:
        """Return sanitized ready stream metadata without source URLs or session IDs."""
        request = Request(f"{self.api_url}/v3/paths/list", method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:  # noqa: S310 - configured local gateway
                payload = json.load(response)
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise MediaMTXError("paths_failed") from exc
        paths: list[dict] = []
        for item in payload.get("items", []):
            name = str(item.get("name", ""))
            if not item.get("ready") or not _CAMERA_ID.fullmatch(name):
                continue
            tracks = []
            for track in item.get("tracks2", []) or []:
                if not isinstance(track, dict):
                    continue
                props = track.get("codecProps") if isinstance(track.get("codecProps"), dict) else {}
                tracks.append({
                    "codec": str(track.get("codec", "")),
                    "width": props.get("width"),
                    "height": props.get("height"),
                })
            paths.append({"name": name, "tracks": tracks})
        return sorted(paths, key=lambda item: item["name"])
