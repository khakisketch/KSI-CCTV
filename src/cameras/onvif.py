"""Bounded ONVIF WS-Discovery with an optional media-profile adapter."""
from __future__ import annotations

import socket
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit, urlunsplit


class WsDiscoveryClient:
    """Minimal local-network WS-Discovery client; it never scans arbitrary hosts."""

    multicast = ("239.255.255.250", 3702)

    def __init__(self, timeout: float = 1.5, socket_factory=socket.socket):
        self.timeout = max(0.1, min(float(timeout), 5.0))
        self.socket_factory = socket_factory

    def discover(self) -> list[dict]:
        message_id = f"uuid:{uuid.uuid4()}"
        payload = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope" '
            'xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing" '
            'xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery" '
            'xmlns:dn="http://www.onvif.org/ver10/network/wsdl">'
            f'<e:Header><w:MessageID>{message_id}</w:MessageID>'
            '<w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>'
            '<w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>'
            '</e:Header><e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types>'
            '</d:Probe></e:Body></e:Envelope>'
        ).encode()
        devices: dict[str, dict] = {}
        with self.socket_factory(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            sock.settimeout(self.timeout)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            sock.sendto(payload, self.multicast)
            while True:
                try:
                    data, address = sock.recvfrom(65535)
                except TimeoutError:
                    break
                device = self._parse(data, address[0])
                if device:
                    devices[device["endpoint"]] = device
        return sorted(devices.values(), key=lambda item: (item.get("host", ""), item["endpoint"]))

    @staticmethod
    def _parse(payload: bytes, source_ip: str) -> dict | None:
        try:
            root = ET.fromstring(payload)
        except ET.ParseError:
            return None

        def text(local_name: str) -> str:
            for element in root.iter():
                if element.tag.rsplit("}", 1)[-1] == local_name and element.text:
                    return element.text.strip()
            return ""

        endpoint = text("Address") or source_ip
        xaddrs = text("XAddrs").split()
        scopes = text("Scopes").split()
        onvif_url = None
        onvif_port = 80
        for xaddr in xaddrs:
            try:
                parsed = urlsplit(xaddr)
                if parsed.scheme in {"http", "https"} and parsed.hostname:
                    onvif_url = xaddr
                    onvif_port = parsed.port or 80
                    break
            except ValueError:
                continue
        return {
            "endpoint": endpoint,
            "host": source_ip,
            "xaddrs": xaddrs,
            "onvif_url": onvif_url,
            "onvif_port": onvif_port,
            "scopes": scopes,
            "name": WsDiscoveryClient._scope_value(scopes, "name") or source_ip,
            "model": WsDiscoveryClient._scope_value(scopes, "hardware"),
        }

    @staticmethod
    def _scope_value(scopes: list[str], key: str) -> str | None:
        marker = f"onvif.org/{key}/"
        for scope in scopes:
            if marker in scope:
                return scope.split(marker, 1)[1].replace("%20", " ")
        return None


class OnvifClient:
    """WS-Discovery plus optional onvif-zeep media-profile enumeration."""

    def __init__(self, discovery: WsDiscoveryClient | None = None):
        self.discovery = discovery or WsDiscoveryClient()

    def discover(self) -> list[dict]:
        return self.discovery.discover()

    def profiles(
        self,
        host: str,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
    ) -> list[dict]:
        try:
            from onvif import ONVIFCamera
        except ImportError as exc:
            raise RuntimeError("onvif-zeep is not installed") from exc

        camera = ONVIFCamera(host, port, username or "", password or "")
        media = camera.create_media_service()
        output: list[dict] = []
        for profile in media.GetProfiles() or []:
            token = str(getattr(profile, "token", ""))
            encoder = getattr(profile, "VideoEncoderConfiguration", None)
            resolution = getattr(encoder, "Resolution", None)
            rate = getattr(encoder, "RateControl", None)
            uri = media.GetStreamUri({
                "StreamSetup": {
                    "Stream": "RTP-Unicast",
                    "Transport": {"Protocol": "RTSP"},
                },
                "ProfileToken": token,
            })
            stream_uri = self._without_credentials(str(getattr(uri, "Uri", "")))
            output.append({
                "token": token,
                "name": str(getattr(profile, "Name", token or "ONVIF profile")),
                "rtsp_url": stream_uri,
                "encoding": str(getattr(encoder, "Encoding", "")) if encoder else "",
                "resolution": [
                    int(getattr(resolution, "Width", 0)),
                    int(getattr(resolution, "Height", 0)),
                ] if resolution else None,
                "fps": float(getattr(rate, "FrameRateLimit", 0.0)) if rate else None,
            })
        return output

    @staticmethod
    def _without_credentials(url: str) -> str:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host += f":{parsed.port}"
        # Query and fragment values often carry vendor-specific stream tokens. Credentials belong only
        # in the encrypted credential store, never in a discoverable or persisted RTSP URL.
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


class OnvifAdapter:
    def __init__(self, client_factory=None, discovery: WsDiscoveryClient | None = None):
        self.client_factory = client_factory
        self.discovery = discovery

    def discover(self) -> dict:
        provider = self.client_factory or self.discovery
        if provider is None:
            return {"available": False, "devices": [], "reason": "ONVIF support is not installed"}
        try:
            return {"available": True, "devices": provider.discover()}
        except Exception as exc:
            return {"available": False, "devices": [], "reason": type(exc).__name__}

    def profiles(
        self,
        host: str,
        port: int = 80,
        username: str | None = None,
        password: str | None = None,
    ) -> dict:
        if self.client_factory is None or not hasattr(self.client_factory, "profiles"):
            return {"available": False, "profiles": [], "reason": "ONVIF support is not installed"}
        try:
            return {
                "available": True,
                "profiles": self.client_factory.profiles(host, port, username, password),
            }
        except Exception as exc:
            return {"available": False, "profiles": [], "reason": type(exc).__name__}
