from __future__ import annotations

import importlib.util
import tempfile
from pathlib import Path

MODULE = Path(__file__).parents[1] / "installer" / "verify_systemd_unit.py"
SPEC = importlib.util.spec_from_file_location("verify_systemd_unit", MODULE)
assert SPEC and SPEC.loader
verify_systemd_unit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify_systemd_unit)


def test_shipped_unit_satisfies_appliance_contract() -> None:
    unit = Path(__file__).parents[1] / "installer" / "systemd" / "ksi-cctv-compose.service"

    assert verify_systemd_unit.validate_unit(unit) == []
    content = unit.read_text(encoding="utf-8")
    assert "--abort-on-container-exit" in content
    assert "--detach" not in content
    assert "Type=exec" in content
    assert "ExecStopPost=-/usr/bin/docker compose" in content
    assert "Restart=always" in content


def test_rejects_shell_exec_start() -> None:
    unit = Path(__file__).parents[1] / "installer" / "systemd" / "ksi-cctv-compose.service"
    with tempfile.TemporaryDirectory() as temp:
        bad = Path(temp) / "bad.service"
        bad.write_text(
            unit.read_text(encoding="utf-8").replace(
                "/usr/bin/docker compose --project-name ksi-cctv --file ",
                "/bin/sh -c /usr/bin/docker compose --project-name ksi-cctv --file ",
                1,
            ),
            encoding="utf-8",
        )

        errors = verify_systemd_unit.validate_unit(bad)

    assert any("must not invoke a shell" in error for error in errors)


def test_rejects_extra_or_duplicate_execution_directives() -> None:
    unit = Path(__file__).parents[1] / "installer" / "systemd" / "ksi-cctv-compose.service"
    with tempfile.TemporaryDirectory() as temp:
        extra = Path(temp) / "extra.service"
        extra.write_text(
            unit.read_text(encoding="utf-8").replace(
                "ExecStart=/usr/bin/docker",
                "ExecReload=/bin/sh -c id\nExecStart=/usr/bin/docker",
                1,
            ),
            encoding="utf-8",
        )
        duplicate = Path(temp) / "duplicate.service"
        duplicate.write_text(
            unit.read_text(encoding="utf-8").replace(
                "ExecStart=", "ExecStart=/usr/bin/true\nExecStart=", 1
            ),
            encoding="utf-8",
        )

        assert any("unexpected directives" in error for error in verify_systemd_unit.validate_unit(extra))
        assert any("unable to read unit" in error for error in verify_systemd_unit.validate_unit(duplicate))
