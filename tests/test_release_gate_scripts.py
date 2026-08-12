from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_release_gate_scripts_parse_and_expose_help() -> None:
    for relative in ("scripts/verify_release_candidate.sh", "scripts/verify_appliance_bundle.sh"):
        path = ROOT / relative
        syntax = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True, check=False)
        assert syntax.returncode == 0, syntax.stderr
        help_result = subprocess.run(["bash", str(path), "--help"], capture_output=True, text=True, check=False)
        assert help_result.returncode == 0, help_result.stderr
        assert "Usage:" in help_result.stdout


def test_strict_release_gate_rejects_browser_skip() -> None:
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/verify_release_candidate.sh"), "--strict", "--skip-browser"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "incompatible" in result.stderr


def test_compose_requires_an_explicit_capture_key_for_encrypted_evidence() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "KSI_CCTV_CAPTURE_KEY: ${KSI_CCTV_CAPTURE_KEY:?" in compose
    assert "KSI_CCTV_CAPTURE_KEY: ${KSI_CCTV_CAPTURE_KEY:-}" not in compose


def test_compose_forwards_the_optional_local_auth_setup_token() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    assert "KSI_CCTV_WEB_SETUP_TOKEN: ${KSI_CCTV_WEB_SETUP_TOKEN:-}" in compose


def test_release_gate_scopes_test_capture_key_to_compose_rendering_and_runs_auth_reset() -> None:
    gate = (ROOT / "scripts" / "verify_release_candidate.sh").read_text(encoding="utf-8")
    expected_key = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

    assert f"COMPOSE_RENDER_CAPTURE_KEY='{expected_key}'" in gate
    assert "export COMPOSE_RENDER_CAPTURE_KEY" not in gate
    assert "export KSI_CCTV_CAPTURE_KEY" not in gate
    assert gate.count('KSI_CCTV_CAPTURE_KEY="$COMPOSE_RENDER_CAPTURE_KEY" docker compose') == 3
    assert "npm run test:local-auth-reset" in gate


def test_release_gate_type_checks_schema_v3_and_supervision_paths() -> None:
    gate = (ROOT / "scripts" / "verify_release_candidate.sh").read_text(encoding="utf-8")

    for relative in (
        "src/run.py",
        "src/api/app.py",
        "src/incidents/store.py",
        "src/storage/sqlite_observations.py",
        "installer/release_builder.py",
        "installer/activation_health.py",
        "installer/release_contracts.py",
        "scripts/release_attestation.py",
        "scripts/bundle_attestation.py",
    ):
        assert relative in gate


def test_release_gate_never_exposes_ci_signing_credentials_to_candidate_processes() -> None:
    gate = (ROOT / "scripts" / "verify_release_candidate.sh").read_text(encoding="utf-8")

    assert "run the gate without signing credentials" in gate
    assert "attestation_args+=(--private-key" not in gate


def test_strict_release_gate_rejects_ignored_docker_context_inputs() -> None:
    gate = (ROOT / "scripts" / "verify_release_candidate.sh").read_text(encoding="utf-8")

    assert "git ls-files --others --ignored --exclude-standard -- src frontend/src frontend/public" in gate
    assert "production Docker contexts contain ignored files" in gate


def test_appliance_host_dependencies_are_pinned_and_audited() -> None:
    requirements = (ROOT / "installer" / "requirements.txt").read_text(encoding="utf-8")
    wheelhouse = (ROOT / "installer" / "wheelhouse.sha256").read_text(encoding="utf-8").splitlines()
    gate = (ROOT / "scripts" / "verify_release_candidate.sh").read_text(encoding="utf-8")

    for package in (
        "boolean.py==5.0",
        "cffi==2.1.0",
        "cryptography==49.0.0",
        "license-expression==30.4.4",
        "pycparser==3.0",
        "PyYAML==6.0.2",
    ):
        assert package in requirements
    assert requirements.count("--hash=sha256:") == 6
    assert len(wheelhouse) == 6
    assert all(len(line.split()[0]) == 64 for line in wheelhouse)
    requirement_hashes = {
        line.split("sha256:", 1)[1]
        for line in requirements.splitlines()
        if "--hash=sha256:" in line
    }
    assert requirement_hashes == {line.split()[0] for line in wheelhouse}
    assert "pip_audit --no-deps -r installer/requirements.txt" in gate
    assert 'pip-audit-installer.json"' in gate
    assert "installer/ksi-cctv-appliance-launcher" in gate


def test_appliance_launcher_is_bound_to_the_pinned_host_venv() -> None:
    launcher = (ROOT / "installer" / "ksi-cctv-appliance-launcher").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "offline-appliance.md").read_text(encoding="utf-8")

    assert "/opt/ksi-cctv/installer-venv/bin/python" in launcher
    assert "/opt/ksi-cctv/installer/ksi_cctv_appliance.py" in launcher
    assert "--require-hashes" in docs
    assert "wheelhouse.sha256" in docs


def test_host_tool_installer_packages_every_canonical_runtime_input() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "host"
        result = subprocess.run(
            ["bash", str(ROOT / "installer" / "install_host_tools.sh"), "--root", str(root)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        installer_root = root / "opt" / "ksi-cctv" / "installer"
        for name in (
            "ksi_cctv_appliance.py",
            "release_contracts.py",
            "verify_systemd_unit.py",
            "activation_health.py",
            "transaction_guard.py",
            "compose.appliance.yaml.template",
            "Caddyfile",
            "requirements.txt",
            "wheelhouse.sha256",
        ):
            assert (installer_root / name).is_file()
        assert (root / "opt" / "ksi-cctv" / "config" / "mediamtx.yml").is_file()
        assert (installer_root / "systemd" / "ksi-cctv-compose.service").is_file()
        help_result = subprocess.run(
            [sys.executable, str(installer_root / "ksi_cctv_appliance.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert help_result.returncode == 0, help_result.stderr
        validator = subprocess.run(
            [sys.executable, str(installer_root / "verify_systemd_unit.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        assert validator.returncode == 0, validator.stderr
