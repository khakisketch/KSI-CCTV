#!/usr/bin/env python3
"""Validate pip-audit JSON and summarize packages with known vulnerabilities."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def audit_summary(payloads: list[Any]) -> dict[str, Any]:
    blocked: list[dict[str, Any]] = []
    for payload in payloads:
        dependencies = payload.get("dependencies") if isinstance(payload, dict) else payload
        if not isinstance(dependencies, list):
            raise ValueError("pip-audit JSON has no dependency list")
        for item in dependencies:
            if not isinstance(item, dict):
                raise ValueError("pip-audit JSON has an invalid dependency entry")
            vulnerabilities = item.get("vulns")
            if not isinstance(vulnerabilities, list):
                raise ValueError("pip-audit JSON has no vulnerability list")
            ids = sorted({
                str(vulnerability["id"])
                for vulnerability in vulnerabilities
                if isinstance(vulnerability, dict) and isinstance(vulnerability.get("id"), str)
            })
            if vulnerabilities and not ids:
                raise ValueError("pip-audit JSON has an invalid vulnerability entry")
            if ids:
                blocked.append({
                    "package": str(item.get("name", "unknown")),
                    "version": str(item.get("version", "unknown")),
                    "vulnerabilities": ids,
                })
    return {"blocked": sorted(blocked, key=lambda item: (item["package"], item["version"]))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate pip-audit evidence")
    parser.add_argument("--audit", action="append", required=True, type=Path)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in args.audit]
        summary = audit_summary(payloads)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot evaluate pip audit: {exc}", file=sys.stderr)
        return 2
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    for finding in summary["blocked"]:
        print(f"BLOCKED: {finding['package']} {finding['version']} {','.join(finding['vulnerabilities'])}")
    return 1 if summary["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
