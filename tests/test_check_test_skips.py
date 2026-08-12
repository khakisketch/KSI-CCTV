from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

MODULE = Path(__file__).parents[1] / "scripts" / "check_test_skips.py"
SPEC = importlib.util.spec_from_file_location("check_test_skips", MODULE)
assert SPEC and SPEC.loader
check_test_skips = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_test_skips
SPEC.loader.exec_module(check_test_skips)


def _junit(records: list[tuple[str, str, str]]) -> str:
    cases = "".join(
        f'<testcase classname="{classname}" name="{name}"><skipped message="{reason}" /></testcase>'
        for classname, name, reason in records
    )
    return f'<?xml version="1.0"?><testsuite>{cases}</testsuite>'


def _approved_junit_records() -> list[tuple[str, str, str]]:
    return [
        (*test.rsplit("::", 1), reason)
        for test, reason in check_test_skips.APPROVED_SKIPS
    ]


def test_collect_skips_approves_only_exact_test_and_reason_pairs() -> None:
    records = _approved_junit_records()
    records.append(
        (
            "tests.test_virtual_cctv_lab",
            "test_unapproved_reuse",
            "Docker integration test is opt-in: KSI_CCTV_LAB_INTEGRATION=1",
        )
    )
    xml = """<?xml version=\"1.0\"?>
<testsuite />"""
    xml = _junit(records)
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "results.xml"
        path.write_text(xml, encoding="utf-8")
        collected = check_test_skips.collect_skips(path)

    assert [record.approved for record in collected] == [True] * 5 + [False]
    assert collected[-1].test == "tests.test_virtual_cctv_lab::test_unapproved_reuse"


def test_main_rejects_missing_and_repeated_approved_skip(tmp_path: Path) -> None:
    records = _approved_junit_records()
    records.pop()
    records.append(records[0])
    junit = tmp_path / "results.xml"
    summary = tmp_path / "summary.json"
    pytest_summary = tmp_path / "pytest-summary.json"
    junit.write_text(_junit(records), encoding="utf-8")

    assert check_test_skips.main([
        "--junit", str(junit), "--json-out", str(summary),
        "--pytest-json-out", str(pytest_summary),
    ]) == 1

    result = __import__("json").loads(summary.read_text(encoding="utf-8"))
    assert result["missing"] == [
        {"test": check_test_skips.APPROVED_SKIPS[-1][0], "reason": check_test_skips.APPROVED_SKIPS[-1][1]}
    ]
    assert result["reused"] == [
        {"test": check_test_skips.APPROVED_SKIPS[0][0], "reason": check_test_skips.APPROVED_SKIPS[0][1]}
    ]
    pytest_result = __import__("json").loads(pytest_summary.read_text(encoding="utf-8"))
    assert pytest_result == {
        "errors": 0,
        "failed": 0,
        "passed": 0,
        "schema_version": 1,
        "skipped": 5,
        "tests": 5,
    }


def test_collect_skips_normalizes_pytest_junit_location_prefix(tmp_path: Path) -> None:
    test, reason = check_test_skips.APPROVED_SKIPS[0]
    classname, name = test.rsplit("::", 1)
    junit = tmp_path / "results.xml"
    junit.write_text(
        f'<testsuite><testcase classname="{classname}" name="{name}">'
        f'<skipped message="{reason} /checkout/tests/test_daemon_smoke.py:43: {reason}">'
        f'/checkout/tests/test_daemon_smoke.py:43: {reason}</skipped></testcase></testsuite>',
        encoding="utf-8",
    )

    records = check_test_skips.collect_skips(junit, (check_test_skips.APPROVED_SKIPS[0],))

    assert records == [check_test_skips.SkipRecord(test=test, reason=reason, approved=True)]
