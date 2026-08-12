"""Bounded TCP RTSP reachability probe with LAN-only SSRF protection."""
from __future__ import annotations

import ipaddress
import socket
from time import monotonic
from urllib.parse import quote, unquote, urlsplit, urlunsplit

_PRIVATE_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
)


class RtspProbe:
    def __init__(
        self,
        resolver=socket.getaddrinfo,
        connector=socket.create_connection,
        allow_loopback: bool = False,
    ):
        self.resolver = resolver
        self.connector = connector
        self.allow_loopback = allow_loopback

    def run(
        self,
        url: str,
        timeout: float = 2.0,
        username: str | None = None,
        password: str | None = None,
        verify_stream: bool = False,
        validated_addresses: list[str] | None = None,
    ) -> dict:
        started = monotonic()
        parsed = urlsplit(url)
        if parsed.scheme not in {"rtsp", "rtsps"} or not parsed.hostname:
            return self._result(False, "invalid_url", "RTSP URL is invalid", started)
        try:
            ips = (
                sorted(set(validated_addresses))
                if validated_addresses is not None
                else self.allowed_host(parsed.hostname, parsed.port or 554)
            )
            if ips and not all(self._is_allowed(ip) for ip in ips):
                ips = []
            if not ips:
                return self._result(False, "ssrf_denied", "target must resolve only to private LAN addresses", started)
            with self.connector((ips[0], parsed.port or 554), timeout=timeout):
                if verify_stream:
                    # Do not let OpenCV resolve the hostname again after validation.
                    stream_url = self.pin_url(self.with_credentials(url, username, password), ips[0])
                    return self._probe_frame(stream_url, timeout, started, ips)
                return self._result(True, "connected", "TCP connection established", started, ips)
        except socket.gaierror:
            return self._result(False, "dns_failed", "host could not be resolved", started)
        except TimeoutError:
            return self._result(False, "timeout", "connection timed out", started)
        except OSError as exc:
            return self._result(False, "connect_failed", type(exc).__name__, started)

    def allowed_host(self, host: str, port: int) -> list[str]:
        answers = self.resolver(host, port, type=socket.SOCK_STREAM)
        ips = sorted({answer[4][0] for answer in answers})
        return ips if ips and all(self._is_allowed(ip) for ip in ips) else []

    @staticmethod
    def pin_url(url: str, address: str) -> str:
        """Return a URL whose connection target is the already-validated address.

        OpenCV's FFMPEG wrapper has no per-connection DNS pinning or Host-header
        override API. Frame verification uses this IP URL, retaining credentials
        and path but not RTSP virtual-host hostname semantics.
        """
        parsed = urlsplit(url)
        host = f"[{address}]" if ":" in address else address
        if parsed.port:
            host += f":{parsed.port}"
        if parsed.username is not None:
            # `urlsplit().username/password` retain percent escapes. Decode
            # exactly once before rebuilding so credentials are not encoded twice.
            auth = quote(unquote(parsed.username), safe="")
            if parsed.password is not None:
                auth += ":" + quote(unquote(parsed.password), safe="")
            host = f"{auth}@{host}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))

    def _is_allowed(self, value: str) -> bool:
        ip = ipaddress.ip_address(value)
        if ip.is_loopback:
            return self.allow_loopback
        return any(ip in network for network in _PRIVATE_NETWORKS)

    @staticmethod
    def with_credentials(url: str, username: str | None, password: str | None) -> str:
        if not username and not password:
            return url
        parsed = urlsplit(url)
        auth = quote(username or "", safe="")
        if password is not None:
            auth += ":" + quote(password, safe="")
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme, f"{auth}@{host}", parsed.path, parsed.query, parsed.fragment))

    def _probe_frame(
        self,
        stream_url: str,
        timeout: float,
        started: float,
        addresses: list[str],
    ) -> dict:
        try:
            import cv2

            capture = cv2.VideoCapture()
            capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(timeout * 1000))
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(timeout * 1000))
            opened = capture.open(stream_url, cv2.CAP_FFMPEG)
            ok, frame = capture.read() if opened else (False, None)
            if not opened:
                return self._result(False, "rtsp_open_failed", "RTSP session could not be opened", started, addresses)
            if not ok or frame is None:
                return self._result(False, "no_frame", "RTSP opened but no frame was received", started, addresses)
            result = self._result(True, "first_frame", "First video frame received", started, addresses)
            result["diagnostics"] = {
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "fps": round(float(capture.get(cv2.CAP_PROP_FPS) or 0.0), 2),
                "first_frame": True,
            }
            return result
        except Exception as exc:
            return self._result(False, "stream_error", type(exc).__name__, started, addresses)
        finally:
            if "capture" in locals():
                capture.release()

    @staticmethod
    def _result(ok: bool, code: str, message: str, started: float, addresses: list[str] | None = None) -> dict:
        result = {"ok": ok, "code": code, "message": message, "latency_ms": round((monotonic() - started) * 1000, 2)}
        if addresses:
            result["addresses"] = addresses
        result.setdefault("diagnostics", {"tcp_connected": ok})
        return result
