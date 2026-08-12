from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE = Path(__file__).parents[1] / "scripts" / "release_attestation.py"
SPEC = importlib.util.spec_from_file_location("release_attestation", MODULE)
assert SPEC and SPEC.loader
release_attestation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_attestation
SPEC.loader.exec_module(release_attestation)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _write(path: Path, content: str = "release input\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _release_root(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir(parents=True)
    for relative in (*release_attestation.KEY_LOCK_PATHS, *release_attestation.CONFIG_PATHS):
        _write(root / relative)
    _write(root / release_attestation.RELEASE_SCRIPT, "#!/usr/bin/env bash\n")
    _write(root / "tracked-source.txt")
    _git(root, "init")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Attestation Test")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "release input")
    return root


def _evidence(artifact_dir: Path) -> list[str]:
    payloads = {
        "pytest": {
            "schema_version": 1,
            "tests": 105,
            "passed": 100,
            "failed": 0,
            "errors": 0,
            "skipped": len(release_attestation.APPROVED_SKIPS),
        },
        "skip": {
            "skip_count": len(release_attestation.APPROVED_SKIPS),
            "approved_count": len(release_attestation.APPROVED_SKIPS),
            "missing": [],
            "unexpected": [],
            "reused": [],
            "records": [
                {"test": test, "reason": reason, "approved": True}
                for test, reason in release_attestation.APPROVED_SKIPS
            ],
        },
        "pip": {"blocked": []},
        "npm": {"maximum_allowed": "high", "blocked": []},
        "playwright": {"schema_version": 1, "passed": 10, "skipped": 0},
    }
    result: list[str] = []
    for kind in release_attestation.EVIDENCE_KINDS:
        path = artifact_dir / release_attestation.EVIDENCE_SUMMARY_PATHS[kind]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payloads[kind], sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n",
            encoding="utf-8",
        )
        result.append(f"{kind}={path}")
    return result


def _keypair(tmp_path: Path) -> tuple[Path, Path]:
    private_key = Ed25519PrivateKey.generate()
    private_path = tmp_path / "ci-private.pem"
    public_path = tmp_path / "trusted-release-public.pem"
    private_path.write_bytes(
        private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    os.chmod(private_path, 0o600)
    current = private_path.parent
    while current != current.parent:
        metadata = current.stat()
        if metadata.st_uid != os.getuid():
            break
        os.chmod(current, 0o700)
        current = current.parent
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


def _attestation(tmp_path: Path, strict: bool = True, sign: bool = True) -> tuple[Path, Path, Path, Path]:
    root = _release_root(tmp_path)
    artifact_dir = tmp_path / "evidence"
    output = artifact_dir / "release-attestation.json"
    private_key, public_key = _keypair(tmp_path)
    command = (
        "bash scripts/verify_release_candidate.sh --strict"
        if strict
        else "bash scripts/verify_release_candidate.sh"
    )
    release_attestation.initialize(output, root, strict, command)
    finalize_args = [output, root, True, _evidence(artifact_dir)]
    if sign:
        finalize_args.extend([private_key, output.with_suffix(".json.sig")])
    release_attestation.finalize(*finalize_args)
    return root, output, public_key, output.with_suffix(".json.sig")


def test_signed_strict_attestation_verifies(tmp_path: Path) -> None:
    root, output, public_key, signature = _attestation(tmp_path)

    release_attestation.verify(output, root, public_key, signature)


def test_attestation_locks_exact_installer_dependency_inputs() -> None:
    assert {
        "installer/requirements.in",
        "installer/requirements.txt",
        "installer/wheelhouse.sha256",
    }.issubset(release_attestation.KEY_LOCK_PATHS)


def test_attestation_rejects_one_artifact_reused_across_evidence_kinds(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    shared = output.parent / "shared.json"
    _write(shared, "shared")
    release_attestation.initialize(output, root, True, "bash scripts/verify_release_candidate.sh --strict")

    with pytest.raises(
        release_attestation.AttestationError,
        match="canonical summary path|distinct artifact paths",
    ):
        release_attestation.finalize(
            output,
            root,
            True,
            [f"{kind}={shared}" for kind in release_attestation.EVIDENCE_KINDS],
        )


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_attestation_finalizer_rejects_filesystem_evidence_aliases(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    evidence = _evidence(output.parent)
    pytest_path = output.parent / release_attestation.EVIDENCE_SUMMARY_PATHS["pytest"]
    alias = output.parent / f"pytest-{alias_kind}.json"
    if alias_kind == "symlink":
        alias.symlink_to(pytest_path.name)
    else:
        os.link(pytest_path, alias)
    evidence = [f"skip={alias}" if item.startswith("skip=") else item for item in evidence]
    release_attestation.initialize(output, root, True, "bash scripts/verify_release_candidate.sh --strict")

    with pytest.raises(
        release_attestation.AttestationError,
        match="symlink|canonical summary path|distinct artifact identities|single-link",
    ):
        release_attestation.finalize(output, root, True, evidence)


def test_attestation_verifier_rejects_lexical_evidence_aliases(tmp_path: Path) -> None:
    _root, output, _public_key, _signature = _attestation(tmp_path, sign=False)
    payload = json.loads(output.read_text(encoding="utf-8"))
    pytest_mapping = payload["evidence_sha256"]["pytest"]
    pytest_path, pytest_digest = next(iter(pytest_mapping.items()))
    payload["evidence_sha256"]["skip"] = {f"./{pytest_path}": pytest_digest}

    with pytest.raises(
        release_attestation.AttestationError,
        match="canonical relative|canonical summary",
    ):
        release_attestation._verify_evidence(output, payload["evidence_sha256"])


def test_attestation_reader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    output = tmp_path / "release-attestation.json"
    output.write_text('{"schema_version":1,"schema_version":1}\n', encoding="utf-8")

    with pytest.raises(release_attestation.AttestationError, match="duplicate JSON key"):
        release_attestation._read_attestation(output)


def test_attestation_atomic_write_does_not_follow_temporary_symlink(tmp_path: Path) -> None:
    output = tmp_path / "release-attestation.json"
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged\n", encoding="utf-8")
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.symlink_to(victim.name)

    with pytest.raises(release_attestation.AttestationError, match="write attestation output safely"):
        release_attestation._write_json(output, {"schema_version": 1})
    assert victim.read_text(encoding="utf-8") == "unchanged\n"


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
def test_attestation_verifier_rejects_filesystem_evidence_aliases(
    tmp_path: Path,
    alias_kind: str,
) -> None:
    _root, output, _public_key, _signature = _attestation(tmp_path, sign=False)
    payload = json.loads(output.read_text(encoding="utf-8"))
    pytest_path, pytest_digest = next(iter(payload["evidence_sha256"]["pytest"].items()))
    source = output.parent / pytest_path
    alias = output.parent / f"pytest-{alias_kind}.json"
    if alias_kind == "symlink":
        alias.symlink_to(source.name)
    else:
        os.link(source, alias)
    payload["evidence_sha256"]["skip"] = {alias.name: pytest_digest}

    with pytest.raises(
        release_attestation.AttestationError,
        match="symlink|canonical summary|distinct artifact identities|single-link",
    ):
        release_attestation._verify_evidence(output, payload["evidence_sha256"])


def test_finalized_gate_is_signed_in_a_separate_trusted_step(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    private_key, public_key = _keypair(tmp_path)
    signature = output.with_suffix(".json.sig")
    release_attestation.initialize(output, root, True, "bash scripts/verify_release_candidate.sh --strict")
    release_attestation.finalize(output, root, True, _evidence(output.parent))

    release_attestation.sign_finalized(output, root, private_key, signature)
    release_attestation.verify(output, root, public_key, signature)


def test_strict_attestation_cannot_pass_after_release_input_changes(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    release_attestation.initialize(output, root, True, "bash scripts/verify_release_candidate.sh --strict")
    _write(root / "tracked-source.txt", "changed during gate\n")

    with pytest.raises(release_attestation.AttestationError, match="changed"):
        release_attestation.finalize(output, root, True, _evidence(output.parent))


def test_verifier_rejects_tampered_or_missing_evidence(tmp_path: Path) -> None:
    root, output, public_key, signature = _attestation(tmp_path)
    evidence = output.parent / release_attestation.EVIDENCE_SUMMARY_PATHS["pytest"]
    _write(evidence, "tampered")

    with pytest.raises(release_attestation.AttestationError, match="tampered evidence"):
        release_attestation.verify(output, root, public_key, signature)

    evidence.unlink()
    with pytest.raises(release_attestation.AttestationError, match="missing evidence"):
        release_attestation.verify(output, root, public_key, signature)


def test_verifier_rejects_non_strict_and_dirty_checkouts(tmp_path: Path) -> None:
    root, output, public_key, signature = _attestation(tmp_path, strict=False, sign=False)
    assert release_attestation._read_attestation(output)["passed"] is True
    assert not signature.exists()

    with pytest.raises(release_attestation.AttestationError, match="strict"):
        release_attestation.verify(output, root, public_key, signature)

    root, output, public_key, signature = _attestation(tmp_path / "dirty")
    _write(root / "tracked-source.txt", "modified\n")
    with pytest.raises(release_attestation.AttestationError, match="dirty"):
        release_attestation.verify(output, root, public_key, signature)


def test_verifier_rejects_mismatched_commit_and_malformed_signature(tmp_path: Path) -> None:
    root, output, public_key, signature = _attestation(tmp_path)
    _write(root / "tracked-source.txt", "next commit\n")
    _git(root, "add", "tracked-source.txt")
    _git(root, "commit", "-m", "next commit")

    with pytest.raises(release_attestation.AttestationError, match="commit"):
        release_attestation.verify(output, root, public_key, signature)

    root, output, public_key, signature = _attestation(tmp_path / "signature")
    signature.write_text("not base64!", encoding="ascii")
    with pytest.raises(release_attestation.AttestationError, match="signature"):
        release_attestation.verify(output, root, public_key, signature)


def test_gate_signer_rejects_permissive_or_checkout_private_key(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    release_attestation.initialize(output, root, True, release_attestation.STRICT_GATE_COMMAND)
    private_key, _ = _keypair(tmp_path)
    os.chmod(private_key, 0o644)
    with pytest.raises(release_attestation.AttestationError, match="private key"):
        release_attestation.finalize(
            output, root, True, _evidence(output.parent), private_key, output.with_suffix(".json.sig")
        )

    checkout_key, _ = _keypair(root)
    with pytest.raises(release_attestation.AttestationError, match="outside"):
        release_attestation.finalize(
            output, root, True, _evidence(output.parent), checkout_key, output.with_suffix(".json.sig")
        )


def test_private_key_requires_owner_and_safe_operator_ancestors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    private_key, _public_key = _keypair(key_dir)
    os.chmod(key_dir, 0o700)

    original_open = release_attestation.os.open
    opened_key: list[str] = []

    def counting_open(path: object, *args: object, **kwargs: object) -> int:
        if path == private_key.name:
            opened_key.append(str(path))
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(release_attestation.os, "open", counting_open)
    release_attestation._load_private_key(private_key)
    assert opened_key == [private_key.name]

    actual_uid = os.getuid()
    monkeypatch.setattr(release_attestation.os, "getuid", lambda: actual_uid + 1)
    with pytest.raises(release_attestation.AttestationError, match="ownership"):
        release_attestation._load_private_key(private_key)
    monkeypatch.undo()

    os.chmod(key_dir, 0o770)
    with pytest.raises(release_attestation.AttestationError, match="ancestor"):
        release_attestation._load_private_key(private_key)


def test_private_key_rejects_symlinked_ancestor(tmp_path: Path) -> None:
    key_dir = tmp_path / "keys"
    key_dir.mkdir()
    private_key, _public_key = _keypair(key_dir)
    alias = tmp_path / "key-alias"
    alias.symlink_to(key_dir, target_is_directory=True)

    with pytest.raises(release_attestation.AttestationError, match="ancestor"):
        release_attestation._load_private_key(alias / private_key.name)


def test_finalize_detects_worktree_mutation_during_evidence_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    release_attestation.initialize(output, root, True, release_attestation.STRICT_GATE_COMMAND)
    original_mapping = release_attestation._evidence_mapping

    def mutate_after_mapping(*args: object, **kwargs: object) -> dict[str, dict[str, str]]:
        mapping = original_mapping(*args, **kwargs)
        _write(root / "tracked-source.txt", "changed after pre-verification snapshot\n")
        return mapping

    monkeypatch.setattr(release_attestation, "_evidence_mapping", mutate_after_mapping)
    with pytest.raises(release_attestation.AttestationError, match="differs"):
        release_attestation.finalize(output, root, True, _evidence(output.parent))


def test_sign_detects_worktree_mutation_during_evidence_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, output, _public_key, _signature = _attestation(tmp_path, sign=False)
    signer_dir = tmp_path / "signer"
    signer_dir.mkdir()
    private_key, _unused_public_key = _keypair(signer_dir)
    original_verify = release_attestation._verify_evidence

    def mutate_after_verify(*args: object, **kwargs: object) -> None:
        original_verify(*args, **kwargs)
        _write(root / "tracked-source.txt", "changed after signing pre-snapshot\n")

    monkeypatch.setattr(release_attestation, "_verify_evidence", mutate_after_verify)
    with pytest.raises(release_attestation.AttestationError, match="differs"):
        release_attestation.sign_finalized(output, root, private_key, output.with_suffix(".json.sig"))


def test_finalize_rejects_head_transition_during_evidence_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    release_attestation.initialize(
        output,
        root,
        True,
        release_attestation.STRICT_GATE_COMMAND,
    )
    original_mapping = release_attestation._evidence_mapping

    def commit_after_mapping(*args: object, **kwargs: object) -> dict[str, dict[str, str]]:
        mapping = original_mapping(*args, **kwargs)
        _write(root / "tracked-source.txt", "new committed source\n")
        _git(root, "add", "tracked-source.txt")
        _git(root, "commit", "-m", "concurrent transition")
        return mapping

    monkeypatch.setattr(release_attestation, "_evidence_mapping", commit_after_mapping)
    with pytest.raises(release_attestation.AttestationError, match="attested commit"):
        release_attestation.finalize(output, root, True, _evidence(output.parent))


def test_finalizer_rejects_nonpassing_summary(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    output = tmp_path / "evidence" / "release-attestation.json"
    evidence = _evidence(output.parent)
    pytest_summary = output.parent / release_attestation.EVIDENCE_SUMMARY_PATHS["pytest"]
    payload = json.loads(pytest_summary.read_text(encoding="utf-8"))
    payload["failed"] = 1
    payload["passed"] -= 1
    pytest_summary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    release_attestation.initialize(
        output,
        root,
        True,
        release_attestation.STRICT_GATE_COMMAND,
    )

    with pytest.raises(release_attestation.AttestationError, match="clean complete run"):
        release_attestation.finalize(output, root, True, evidence)


def test_worktree_snapshot_checks_executable_mode(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    os.chmod(root / "tracked-source.txt", 0o755)

    with pytest.raises(release_attestation.AttestationError, match="differs"):
        release_attestation._worktree_matches_head_and_index(root)
