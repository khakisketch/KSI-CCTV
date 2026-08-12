#!/usr/bin/env python3
"""Validate Playwright JSON and emit a minimal release-safe result summary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def summarize(payload: Any) -> dict[str, int]:
    if not isinstance(payload, dict) or payload.get("config") is None:
        raise ValueError("Playwright JSON report is malformed")
    stats = payload.get("stats")
    required = {"expected", "skipped", "unexpected", "flaky"}
    if (
        not isinstance(stats, dict)
        or not required.issubset(stats)
        or any(
            not isinstance(stats[key], int) or isinstance(stats[key], bool) or stats[key] < 0
            for key in required
        )
    ):
        raise ValueError("Playwright JSON report has invalid result counts")
    if stats["expected"] < 1 or stats["skipped"] or stats["unexpected"] or stats["flaky"]:
        raise ValueError("Playwright report is not a clean passing run")
    return {
        "schema_version": 1,
        "passed": stats["expected"],
        "skipped": stats["skipped"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create sanitized Playwright release evidence")
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--json-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.report.read_text(encoding="utf-8"))
        summary = summarize(payload)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: cannot evaluate Playwright report: {exc}", file=sys.stderr)
        return 2
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(summary, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
