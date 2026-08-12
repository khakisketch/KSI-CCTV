#!/usr/bin/env python3
"""Validate the static contract of the KSI-CCTV Appliance systemd unit."""
from __future__ import annotations

import argparse
import configparser
import json
import sys
from collections.abc import Sequence
from pathlib import Path

EXPECTED = {
    "Unit": {
        "Description": "KSI-CCTV offline appliance compose stack",
        "Requires": "docker.service",
        "After": "docker.service network-online.target",
        "Wants": "network-online.target",
    },
    "Service": {
        "Type": "exec",
        "WorkingDirectory": "/opt/ksi-cctv/current",
        "EnvironmentFile": "-/etc/ksi-cctv/ksi-cctv.env",
        "ExecStartPre": "/usr/bin/python3 /opt/ksi-cctv/current/bin/transaction_guard.py",
        "ExecStart": (
            "/usr/bin/docker compose --project-name ksi-cctv --file "
            "/opt/ksi-cctv/current/compose.yaml up --remove-orphans --abort-on-container-exit"
        ),
        "ExecStartPost": (
            "/usr/bin/python3 /opt/ksi-cctv/current/bin/activation_health.py --timeout 120"
        ),
        "ExecStop": (
            "/usr/bin/docker compose --project-name ksi-cctv --file "
            "/opt/ksi-cctv/current/compose.yaml down"
        ),
        "ExecStopPost": (
            "-/usr/bin/docker compose --project-name ksi-cctv --file "
            "/opt/ksi-cctv/current/compose.yaml down"
        ),
        "TimeoutStartSec": "240",
        "TimeoutStopSec": "90",
        "Restart": "always",
        "RestartSec": "5",
    },
    "Install": {"WantedBy": "multi-user.target"},
}
_SHELL_TOKENS = ("/bin/sh", "bash -c", "&&", "||", ";", "|", "$(", "`")


class CaseSensitiveParser(configparser.RawConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def validate_unit(path: Path) -> list[str]:
    """Return contract violations without invoking systemd or Docker."""
    parser = CaseSensitiveParser(interpolation=None, strict=True)
    try:
        with path.open(encoding="utf-8") as source:
            parser.read_file(source)
    except (OSError, configparser.Error) as exc:
        return [f"unable to read unit: {type(exc).__name__}"]

    errors: list[str] = []
    if set(parser.sections()) != set(EXPECTED):
        errors.append("unit sections must match the Appliance contract exactly")
    for section, values in EXPECTED.items():
        if not parser.has_section(section):
            errors.append(f"missing [{section}] section")
            continue
        for key, expected in values.items():
            actual = parser.get(section, key, fallback=None)
            if actual != expected:
                errors.append(f"[{section}] {key} must be {expected!r}")
        unexpected = set(parser.options(section)) - set(values)
        if unexpected:
            errors.append(f"[{section}] unexpected directives: {', '.join(sorted(unexpected))}")
    if parser.has_section("Service"):
        for key in ("ExecStartPre", "ExecStart", "ExecStartPost", "ExecStop", "ExecStopPost"):
            value = parser.get("Service", key, fallback="")
            if any(token in value for token in _SHELL_TOKENS):
                errors.append(f"[Service] {key} must not invoke a shell")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate KSI-CCTV Appliance systemd unit contract")
    parser.add_argument(
        "--unit",
        default=Path(__file__).with_name("systemd") / "ksi-cctv-compose.service",
        type=Path,
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    errors = validate_unit(args.unit)
    if args.json:
        print(json.dumps({"ok": not errors, "unit": str(args.unit), "errors": errors}))
    elif errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
    else:
        print(f"OK: {args.unit}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
