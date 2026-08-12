#!/usr/bin/env python3
"""Wait for the three appliance containers without exposing container env."""
from __future__ import annotations

import argparse
import http.client
import json
import os
import ssl
import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Any

CONTAINERS = {
    "ksi-cctv": True,
    "ksi-cctv-web": True,
    "ksi-cctv-gateway": False,
    "ksi-cctv-https": True,
}
REQUIRED_HTTPS_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; "
        "form-action 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
        "upgrade-insecure-requests"
    ),
}


def inspect_container(
    name: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any] | None:
    result = runner(
        ["docker", "inspect", "--format", "{{json .State}}", name],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=5,
    )
    if result.returncode:
        return None
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def healthy(states: dict[str, dict[str, Any] | None]) -> bool:
    for name, requires_health in CONTAINERS.items():
        state = states.get(name)
        if not state or not state.get("Running"):
            return False
        if requires_health and (state.get("Health") or {}).get("Status") != "healthy":
            return False
    return True


def https_ready(connection_factory: Callable[[], Any] | None = None) -> bool:
    address = os.environ.get("KSI_CCTV_SITE_ADDRESS", "").strip()
    if not address:
        return False
    factory = connection_factory or (
        lambda: http.client.HTTPSConnection(
            "127.0.0.1",
            443,
            timeout=5,
            context=ssl._create_unverified_context(),
        )
    )
    connection = factory()
    try:
        connection.request("GET", "/login", headers={"Host": address})
        response = connection.getresponse()
        if not 200 <= response.status < 400:
            return False
        return (
            all(response.getheader(name) == expected for name, expected in REQUIRED_HTTPS_HEADERS.items())
            and response.getheader("Server") is None
        )
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return False
    finally:
        connection.close()


def http_redirect_ready(connection_factory: Callable[[], Any] | None = None) -> bool:
    address = os.environ.get("KSI_CCTV_SITE_ADDRESS", "").strip()
    if not address:
        return False
    factory = connection_factory or (
        lambda: http.client.HTTPConnection("127.0.0.1", 80, timeout=5)
    )
    connection = factory()
    try:
        connection.request("GET", "/login", headers={"Host": address})
        response = connection.getresponse()
        return response.status in {301, 302, 307, 308} and response.getheader("Location") == (
            f"https://{address}/login"
        )
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


def wait_until_healthy(timeout: float, interval: float = 1.0) -> tuple[bool, dict[str, str]]:
    deadline = time.monotonic() + max(timeout, 1.0)
    summary: dict[str, str] = {}
    while time.monotonic() < deadline:
        states = {name: inspect_container(name) for name in CONTAINERS}
        summary = {
            name: (
                "missing"
                if state is None
                else str((state.get("Health") or {}).get("Status") or state.get("Status") or "unknown")
            )
            for name, state in states.items()
        }
        if healthy(states) and https_ready() and http_redirect_ready():
            summary["https_proxy"] = "ready"
            summary["http_redirect"] = "ready"
            return True, summary
        summary["https_proxy"] = "not_ready"
        summary["http_redirect"] = "not_ready"
        time.sleep(max(interval, 0.1))
    return False, summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check KSI-CCTV appliance container health")
    parser.add_argument("--timeout", type=float, default=50.0)
    args = parser.parse_args(argv)
    ok, states = wait_until_healthy(args.timeout)
    print(json.dumps({"healthy": ok, "containers": states}, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
