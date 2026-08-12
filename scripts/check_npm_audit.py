#!/usr/bin/env python3
"""Summarize production npm audit JSON and enforce a severity ceiling."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

SEVERITIES = ("low", "moderate", "high", "critical")


def audit_summary(payload: dict[str, Any], maximum: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("npm audit JSON must be an object")
    try:
        threshold = SEVERITIES.index(maximum)
    except ValueError as exc:
        raise ValueError(f"unsupported severity: {maximum}") from exc
    if payload.get("auditReportVersion") != 2:
        raise ValueError("npm audit JSON must use audit report version 2")
    if "vulnerabilities" not in payload or "metadata" not in payload:
        raise ValueError("npm audit JSON has no vulnerabilities or metadata object")
    vulnerabilities = payload["vulnerabilities"]
    if not isinstance(vulnerabilities, dict):
        raise ValueError("npm audit JSON has no vulnerabilities object")
    metadata = payload["metadata"]
    if (
        not isinstance(metadata, dict)
        or not isinstance(metadata.get("vulnerabilities"), dict)
        or not isinstance(metadata.get("dependencies"), dict)
    ):
        raise ValueError("npm audit JSON has no vulnerability metadata object")
    counts = metadata["vulnerabilities"]
    if set(counts) != {*SEVERITIES, "info", "total"} or not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in counts.values()
    ):
        raise ValueError("npm audit vulnerability metadata has invalid counts")
    dependency_keys = {"prod", "dev", "optional", "peer", "peerOptional", "total"}
    if set(metadata["dependencies"]) != dependency_keys or not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in metadata["dependencies"].values()
    ):
        raise ValueError("npm audit dependency metadata has invalid counts")
    findings: list[dict[str, str]] = []
    observed_counts = dict.fromkeys(SEVERITIES, 0)
    for package, item in vulnerabilities.items():
        if not isinstance(package, str) or not package:
            raise ValueError("npm audit vulnerability package name is malformed")
        if not isinstance(item, dict):
            raise ValueError(f"npm audit vulnerability entry is malformed: {package}")
        required = {"name", "severity", "isDirect", "via", "effects", "range", "nodes", "fixAvailable"}
        if not required.issubset(item):
            raise ValueError(f"npm audit vulnerability entry is incomplete: {package}")
        if item["name"] != package or not isinstance(item["severity"], str):
            raise ValueError(f"npm audit vulnerability entry has invalid identity: {package}")
        severity = item["severity"]
        if severity not in SEVERITIES:
            raise ValueError(f"npm audit vulnerability entry has unknown severity: {package}")
        fix = item["fixAvailable"]
        via = item["via"]
        if (
            not isinstance(item["isDirect"], bool)
            or not isinstance(via, list)
            or not isinstance(item["effects"], list)
            or not isinstance(item["range"], str)
            or not isinstance(item["nodes"], list)
            or not isinstance(fix, (bool, dict))
            or not all(isinstance(value, (str, dict)) for value in via)
            or not all(isinstance(value, str) for value in item["effects"] + item["nodes"])
        ):
            raise ValueError(f"npm audit vulnerability entry has invalid types: {package}")
        if isinstance(fix, dict) and (
            set(fix) != {"name", "version", "isSemVerMajor"}
            or not isinstance(fix["name"], str)
            or not isinstance(fix["version"], str)
            or not isinstance(fix["isSemVerMajor"], bool)
        ):
            raise ValueError(f"npm audit vulnerability fix is malformed: {package}")
        for source in via:
            if isinstance(source, dict) and (
                not {"source", "name", "dependency", "title", "url", "severity", "range"}.issubset(source)
                or set(source) - {"source", "name", "dependency", "title", "url", "severity", "range", "cwe", "cvss"}
                or not isinstance(source["source"], int)
                or isinstance(source["source"], bool)
                or not all(
                    isinstance(source[key], str)
                    for key in {"name", "dependency", "title", "url", "severity", "range"}
                )
                or source["severity"] not in SEVERITIES
                or "cwe" in source and (
                    not isinstance(source["cwe"], list)
                    or not all(isinstance(value, str) for value in source["cwe"])
                )
                or "cvss" in source and (
                    not isinstance(source["cvss"], dict)
                    or set(source["cvss"]) != {"score", "vectorString"}
                    or not isinstance(source["cvss"]["score"], (int, float))
                    or isinstance(source["cvss"]["score"], bool)
                    or not isinstance(source["cvss"]["vectorString"], str)
                )
            ):
                raise ValueError(f"npm audit vulnerability via entry is malformed: {package}")
        observed_counts[severity] += 1
        if SEVERITIES.index(severity) < threshold:
            continue
        findings.append({"package": package, "severity": severity})
    if (
        counts["info"] != 0
        or any(counts[severity] != observed_counts[severity] for severity in SEVERITIES)
        or counts["total"] != len(vulnerabilities)
    ):
        raise ValueError("npm audit vulnerability metadata is inconsistent")
    return {"maximum_allowed": maximum, "blocked": sorted(findings, key=lambda item: item["package"])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce a production npm audit severity ceiling")
    parser.add_argument("--audit", required=True, type=Path)
    parser.add_argument("--maximum", choices=SEVERITIES, default="high")
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.audit.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("npm audit JSON must be an object")
        summary = audit_summary(payload, args.maximum)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot evaluate npm audit: {exc}", file=sys.stderr)
        return 2
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    for finding in summary["blocked"]:
        print(f"BLOCKED: {finding['severity']} {finding['package']}")
    return 1 if summary["blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
