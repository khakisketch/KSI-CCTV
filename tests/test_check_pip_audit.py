from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / "scripts" / "check_pip_audit.py"
SPEC = importlib.util.spec_from_file_location("check_pip_audit", MODULE)
assert SPEC and SPEC.loader
check_pip_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = check_pip_audit
SPEC.loader.exec_module(check_pip_audit)


def test_audit_summary_collects_known_vulnerabilities() -> None:
    summary = check_pip_audit.audit_summary([
        {"dependencies": [{"name": "safe", "version": "1.0", "vulns": []}]},
        {"dependencies": [{"name": "vulnerable", "version": "2.0", "vulns": [{"id": "PYSEC-1"}]}]},
    ])

    assert summary == {
        "blocked": [{"package": "vulnerable", "version": "2.0", "vulnerabilities": ["PYSEC-1"]}]
    }


def test_audit_summary_rejects_an_error_response() -> None:
    with pytest.raises(ValueError, match="no dependency list"):
        check_pip_audit.audit_summary([{"message": "audit unavailable"}])
