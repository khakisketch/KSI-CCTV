from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / "scripts" / "check_npm_audit.py"
SPEC = importlib.util.spec_from_file_location("check_npm_audit", MODULE)
assert SPEC and SPEC.loader
check_npm_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_npm_audit
SPEC.loader.exec_module(check_npm_audit)


def _audit(entries: dict[str, dict[str, object]]) -> dict[str, object]:
    counts = dict.fromkeys(check_npm_audit.SEVERITIES, 0)
    for item in entries.values():
        counts[str(item["severity"])] += 1
    return {
        "auditReportVersion": 2,
        "vulnerabilities": entries,
        "metadata": {
            "vulnerabilities": {"info": 0, **counts, "total": len(entries)},
            "dependencies": {
                "prod": 1,
                "dev": 0,
                "optional": 0,
                "peer": 0,
                "peerOptional": 0,
                "total": 1,
            },
        },
    }


def _entry(package: str, severity: str) -> dict[str, object]:
    return {
        "name": package,
        "severity": severity,
        "isDirect": True,
        "via": [],
        "effects": [],
        "range": "*",
        "nodes": [f"node_modules/{package}"],
        "fixAvailable": False,
    }


def test_audit_summary_blocks_high_and_critical_only() -> None:
    result = check_npm_audit.audit_summary(
        _audit(
            {
                "low-package": _entry("low-package", "low"),
                "high-package": _entry("high-package", "high"),
                "critical-package": _entry("critical-package", "critical"),
            }
        ),
        "high",
    )

    assert result["blocked"] == [
        {"package": "critical-package", "severity": "critical"},
        {"package": "high-package", "severity": "high"},
    ]


def _via(package: str, vector_string: object) -> dict[str, object]:
    return {
        "source": 1,
        "name": package,
        "dependency": package,
        "title": "test advisory",
        "url": "https://example.invalid/advisory",
        "severity": "high",
        "range": "*",
        "cvss": {"score": 0, "vectorString": vector_string},
    }


def test_audit_summary_accepts_null_cvss_vector_string() -> None:
    entry = _entry("high-package", "high")
    entry["via"] = [_via("high-package", None)]

    result = check_npm_audit.audit_summary(
        _audit({"high-package": entry}),
        "high",
    )

    assert result["blocked"] == [{"package": "high-package", "severity": "high"}]


def test_audit_summary_rejects_non_string_cvss_vector_string() -> None:
    entry = _entry("high-package", "high")
    entry["via"] = [_via("high-package", 123)]

    with pytest.raises(ValueError, match="via entry is malformed"):
        check_npm_audit.audit_summary(_audit({"high-package": entry}), "high")


def test_audit_summary_rejects_an_audit_error_response() -> None:
    with pytest.raises(ValueError, match="report version"):
        check_npm_audit.audit_summary({"message": "Invalid package tree"}, "high")


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload["vulnerabilities"].update({"bad": []}), "malformed"),
        (lambda payload: payload["vulnerabilities"]["low-package"].update({"severity": "unknown"}), "unknown severity"),
        (lambda payload: payload["vulnerabilities"]["low-package"].update({"nodes": "wrong"}), "invalid types"),
        (lambda payload: payload["metadata"]["vulnerabilities"].update({"low": 2}), "inconsistent"),
        (lambda payload: payload.update({"auditReportVersion": "2"}), "report version"),
    ],
)
def test_audit_summary_fails_closed_on_malformed_audit(
    mutate: object, message: str
) -> None:
    payload = _audit({"low-package": _entry("low-package", "low")})
    mutate(payload)  # type: ignore[operator]

    with pytest.raises(ValueError, match=message):
        check_npm_audit.audit_summary(payload, "high")
