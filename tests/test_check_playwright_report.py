from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MODULE = Path(__file__).parents[1] / "scripts" / "check_playwright_report.py"
SPEC = importlib.util.spec_from_file_location("check_playwright_report", MODULE)
assert SPEC and SPEC.loader
check_playwright_report = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_playwright_report
SPEC.loader.exec_module(check_playwright_report)


def test_summarize_accepts_only_clean_nonempty_run() -> None:
    summary = check_playwright_report.summarize({
        "config": {},
        "stats": {"expected": 10, "skipped": 0, "unexpected": 0, "flaky": 0},
    })

    assert summary == {"schema_version": 1, "passed": 10, "skipped": 0}


def test_summarize_rejects_unexpected_or_malformed_results() -> None:
    for payload in (
        {},
        {"config": {}, "stats": {"expected": 0, "skipped": 0, "unexpected": 0, "flaky": 0}},
        {"config": {}, "stats": {"expected": 9, "skipped": 1, "unexpected": 0, "flaky": 0}},
        {"config": {}, "stats": {"expected": 9, "skipped": 0, "unexpected": 1, "flaky": 0}},
        {"config": {}, "stats": {"expected": 9, "skipped": 0, "unexpected": 0, "flaky": True}},
    ):
        try:
            check_playwright_report.summarize(payload)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe Playwright report was accepted")
