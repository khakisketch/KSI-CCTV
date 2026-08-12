#!/usr/bin/env python3
"""Fail a release gate when pytest reports an unapproved skipped test."""
from __future__ import annotations

import argparse
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))

from release_contracts import APPROVED_SKIPS  # noqa: E402,I001
PYTEST_JUNIT_LOCATION = re.compile(r"^[^\n]+\.py:\d+:\s+(.*)$", re.DOTALL)


@dataclass(frozen=True)
class SkipRecord:
    test: str
    reason: str
    approved: bool


def collect_skips(
    xml_path: Path, approved_skips: tuple[tuple[str, str], ...] = APPROVED_SKIPS
) -> list[SkipRecord]:
    root = ET.parse(xml_path).getroot()
    approved = set(approved_skips)
    records: list[SkipRecord] = []
    for case in root.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None:
            continue
        # Pytest decorates JUnit skip text with ``path:line:``; retain only the supplied reason.
        reason = (skipped.text or "").strip() or skipped.get("message", "").strip()
        if location_match := PYTEST_JUNIT_LOCATION.fullmatch(reason):
            reason = location_match.group(1)
        test = "::".join(filter(None, (case.get("classname", ""), case.get("name", ""))))
        records.append(
            SkipRecord(
                test=test,
                reason=reason,
                approved=(test, reason) in approved,
            )
        )
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate pytest skip reasons for release evidence")
    parser.add_argument("--junit", required=True, type=Path)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--pytest-json-out", type=Path)
    args = parser.parse_args(argv)
    if not args.junit.is_file():
        print(f"error: JUnit result does not exist: {args.junit}", file=sys.stderr)
        return 2
    records = collect_skips(args.junit)
    observed = Counter((record.test, record.reason) for record in records)
    expected = Counter(APPROVED_SKIPS)
    unexpected = [asdict(record) for record in records if not record.approved]
    missing = [
        {"test": test, "reason": reason}
        for (test, reason), count in (expected - observed).items()
        for _ in range(count)
    ]
    reused = [
        {"test": test, "reason": reason}
        for (test, reason), count in (observed - expected).items()
        if (test, reason) in expected
        for _ in range(count)
    ]
    result = {
        "skip_count": len(records),
        "approved_count": sum(record.approved for record in records),
        "missing": missing,
        "unexpected": unexpected,
        "reused": reused,
        "records": [asdict(record) for record in records],
    }
    root = ET.parse(args.junit).getroot()
    cases = list(root.iter("testcase"))
    pytest_summary = {
        "schema_version": 1,
        "tests": len(cases),
        "passed": sum(
            case.find("failure") is None
            and case.find("error") is None
            and case.find("skipped") is None
            for case in cases
        ),
        "failed": sum(case.find("failure") is not None for case in cases),
        "errors": sum(case.find("error") is not None for case in cases),
        "skipped": len(records),
    }
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
    if args.pytest_json_out:
        args.pytest_json_out.parent.mkdir(parents=True, exist_ok=True)
        args.pytest_json_out.write_text(
            json.dumps(pytest_summary, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
    for record in records:
        state = "approved" if record.approved else "UNEXPECTED"
        print(f"{state}: {record.test} - {record.reason}")
    return 0 if not (unexpected or missing or reused or pytest_summary["failed"] or pytest_summary["errors"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
