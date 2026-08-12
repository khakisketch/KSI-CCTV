#!/usr/bin/env python3
"""Create and verify canonical release-gate attestations."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "installer"))

from release_contracts import (  # noqa: E402,I001
    APPROVED_SKIPS as APPROVED_SKIPS,
    EVIDENCE_SUMMARY_PATHS,
    ReleaseContractError,
    STRICT_GATE_COMMAND,
    validate_gate_evidence_summary,
)

SCHEMA_VERSION = 1
RELEASE_SCRIPT = "scripts/verify_release_candidate.sh"
EVIDENCE_KINDS = ("pytest", "skip", "pip", "npm", "playwright")
KEY_LOCK_PATHS = (
    "requirements.txt",
    "requirements-dev.txt",
    "requirements-onvif.txt",
    "installer/requirements.in",
    "installer/requirements.txt",
    "installer/wheelhouse.sha256",
    "frontend/package-lock.json",
)
CONFIG_PATHS = (
    "config/alert_preferences.yaml",
    "config/camera_profiles.yaml",
    "config/default.yaml",
    "config/event_policies.yaml",
    "config/mediamtx.yml",
    "config/model_profiles.yaml",
    "config/prompts/__init__.py",
    "config/prompts/construction.yaml",
    "config/prompts/factory.yaml",
    "config/prompts/general.yaml",
    "config/prompts/parking.yaml",
    "config/prompts/school.yaml",
    "config/prompts/warehouse.yaml",
    "config/proximity.example.yaml",
    "config/zones.example.yaml",
    "docker-compose.yml",
    "docker-compose.gpu.yml",
    "docker-compose.diagnostics.yml",
    "frontend/package.json",
)
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
RUNNER_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
TOOL_VERSION_KEYS = {"python", "git", "node", "npm", "docker", "docker_compose"}


class AttestationError(ValueError):
    """Raised for an invalid or unverifiable attestation."""


def _run_git(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False
    )
    if result.returncode:
        raise AttestationError(result.stderr.decode("utf-8", "replace").strip() or "git failed")
    return result.stdout


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha256_file(path: Path) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AttestationError(f"cannot open evidence safely: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise AttestationError(f"evidence must be a single-link regular file: {path.name}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        identity = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        if identity != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise AttestationError(f"evidence changed while being hashed: {path.name}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _read_stable_bytes(path: Path, *, maximum: int, label: str) -> bytes:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise AttestationError(f"cannot open {label} safely") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > maximum:
            raise AttestationError(f"{label} must be a bounded single-link regular file")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(maximum + 1)
        after = os.fstat(descriptor)
        if len(content) > maximum or (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise AttestationError(f"{label} changed while being read")
        return content
    finally:
        os.close(descriptor)


def _digest_paths(root: Path, paths: tuple[str, ...]) -> dict[str, str]:
    digests: dict[str, str] = {}
    for relative in paths:
        path = root / relative
        if not path.is_file():
            raise AttestationError(f"required release input is missing: {relative}")
        digests[relative] = _sha256_file(path)
    return digests


def tracked_tree_sha256(root: Path) -> str:
    """Hash Git's tracked index records, including path, mode, and blob identity."""
    records = _run_git(root, "ls-files", "-s", "-z").split(b"\0")
    digest = hashlib.sha256()
    for record in records:
        if not record:
            continue
        digest.update(record)
        digest.update(b"\0")
    return digest.hexdigest()


def _git_object_format(root: Path) -> str:
    object_format = _run_git(root, "rev-parse", "--show-object-format").decode().strip()
    if object_format not in {"sha1", "sha256"}:
        raise AttestationError("unsupported Git object format")
    return object_format


def _same_file_identity(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) == (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


def _worktree_blob_oid(path: Path, object_format: str) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if not hasattr(os, "O_NOFOLLOW"):
        raise AttestationError("safe worktree verification requires O_NOFOLLOW")
    try:
        descriptor = os.open(path, flags | os.O_NOFOLLOW)
    except OSError as exc:
        raise AttestationError(f"cannot read tracked worktree file safely: {path}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AttestationError(f"tracked worktree path is not a regular file: {path}")
        digest = hashlib.new(object_format)
        digest.update(f"blob {before.st_size}\0".encode("ascii"))
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if not _same_file_identity(before, os.fstat(descriptor)):
            raise AttestationError(f"tracked worktree file changed while being read: {path}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _head_and_index_records(root: Path) -> dict[str, tuple[str, str]]:
    head_records: dict[str, tuple[str, str]] = {}
    for record in _run_git(root, "ls-tree", "-r", "-z", "HEAD").split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", "surrogateescape")
        except ValueError as exc:
            raise AttestationError("Git HEAD tree contains a malformed record") from exc
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise AttestationError("strict release tree contains unsupported tracked entry")
        head_records[relative] = (mode, object_id)
    index_records: dict[str, tuple[str, str]] = {}
    for record in _run_git(root, "ls-files", "-s", "-z").split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8", "surrogateescape")
        except ValueError as exc:
            raise AttestationError("Git index contains a malformed record") from exc
        if stage != "0":
            raise AttestationError("Git index has unresolved entries")
        index_records[relative] = (mode, object_id)
    if index_records != head_records:
        raise AttestationError("Git index does not exactly match HEAD")
    return head_records


def _worktree_matches_head_and_index(root: Path) -> None:
    """Compare stable worktree bytes and executable bits to fresh HEAD/index records."""
    object_format = _git_object_format(root)
    for relative, (expected_mode, expected_oid) in _head_and_index_records(root).items():
        path = root / relative
        try:
            metadata = os.lstat(path)
        except OSError as exc:
            raise AttestationError(f"tracked worktree file is missing: {relative}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise AttestationError(f"tracked worktree path must not be a symlink: {relative}")
        expected_executable = expected_mode == "100755"
        actual_executable = bool(stat.S_IMODE(metadata.st_mode) & 0o111)
        if actual_executable != expected_executable or _worktree_blob_oid(path, object_format) != expected_oid:
            raise AttestationError(f"tracked worktree file differs from HEAD/index: {relative}")


def _worktree_matches_claimed_commit(root: Path, commit: Any) -> None:
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise AttestationError("attestation commit is malformed")
    if _run_git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise AttestationError("checked-out HEAD changed from the attested commit")
    _worktree_matches_head_and_index(root)
    if _run_git(root, "rev-parse", "HEAD").decode().strip() != commit:
        raise AttestationError("checked-out HEAD changed during worktree verification")


def _clean_tree(root: Path) -> bool:
    return not _run_git(root, "status", "--porcelain", "--untracked-files=all")


def _tool_version(*command: str) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError:
        return "unavailable"
    if result.returncode:
        return "unavailable"
    return (result.stdout or result.stderr).strip() or "unavailable"


def _runner_identifier() -> str:
    raw = os.environ.get("KSI_CCTV_RUNNER_ID") or os.environ.get("CI_RUNNER_ID")
    raw = raw or os.environ.get("HOSTNAME") or "unknown"
    return RUNNER_SAFE.sub("_", raw).strip("._-")[:128] or "unknown"


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _write_atomic(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    temporary_name = f".{path.name}.{os.getpid()}.tmp"
    try:
        parent_fd = os.open(path.parent, directory_flags)
        created = False
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC,
                0o600,
                dir_fd=parent_fd,
            )
            created = True
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), mode)
            os.replace(temporary_name, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            if created:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=parent_fd)
            os.close(parent_fd)
    except OSError as exc:
        raise AttestationError(f"cannot write attestation output safely: {path.name}") from exc


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _write_atomic(path, _canonical_bytes(payload) + b"\n")


def _read_attestation(path: Path) -> dict[str, Any]:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise AttestationError(f"attestation contains a duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        raw = _read_stable_bytes(path, maximum=16 * 1024 * 1024, label="attestation")
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise AttestationError(f"cannot read attestation: {exc}") from exc
    if not isinstance(payload, dict):
        raise AttestationError("attestation must be a JSON object")
    if raw != _canonical_bytes(payload) + b"\n":
        raise AttestationError("attestation must use canonical JSON bytes")
    return payload


def _evidence_path(
    path: Path,
    base: Path,
    *,
    require_file: bool,
) -> tuple[str, os.stat_result]:
    lexical = Path(os.path.abspath(path))
    try:
        relative = lexical.relative_to(base)
    except ValueError as exc:
        raise AttestationError("evidence must be inside the artifact directory") from exc
    current = base
    metadata = os.lstat(base)
    try:
        for part in relative.parts:
            current /= part
            metadata = os.lstat(current)
            if stat.S_ISLNK(metadata.st_mode):
                raise AttestationError("evidence paths must not contain symlinks")
    except OSError as exc:
        raise AttestationError(f"missing evidence: {relative.as_posix()}") from exc
    if require_file and (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1):
        raise AttestationError(f"evidence is not a single-link regular file: {relative.as_posix()}")
    return relative.as_posix(), metadata


def _evidence_mapping(output: Path, evidence: list[str]) -> dict[str, dict[str, str]]:
    artifact_dir = output.parent.resolve()
    mappings: dict[str, dict[str, str]] = {kind: {} for kind in EVIDENCE_KINDS}
    owners: dict[str, str] = {}
    inode_owners: dict[tuple[int, int], str] = {}
    for item in evidence:
        try:
            kind, value = item.split("=", 1)
        except ValueError as exc:
            raise AttestationError(f"invalid evidence mapping: {item}") from exc
        if kind not in mappings:
            raise AttestationError(f"unsupported evidence kind: {kind}")
        source = Path(os.path.abspath(value))
        _evidence_path(source, artifact_dir, require_file=False)
        files = sorted(path for path in source.rglob("*") if path.is_file()) if source.is_dir() else [source]
        if not files:
            raise AttestationError(f"evidence directory is empty: {value}")
        for file_path in files:
            relative, metadata = _evidence_path(file_path, artifact_dir, require_file=True)
            prior_kind = owners.setdefault(relative, kind)
            if prior_kind != kind:
                raise AttestationError("evidence kinds must use distinct artifact paths")
            prior_inode_kind = inode_owners.setdefault((metadata.st_dev, metadata.st_ino), kind)
            if prior_inode_kind != kind:
                raise AttestationError("evidence kinds must use distinct artifact identities")
            try:
                validate_gate_evidence_summary(file_path, kind)
            except ReleaseContractError as exc:
                raise AttestationError(str(exc)) from exc
            mappings[kind][relative] = _sha256_file(file_path)
    if any(set(mapping) != {EVIDENCE_SUMMARY_PATHS[kind]} for kind, mapping in mappings.items()):
        raise AttestationError("release evidence must use one canonical summary per kind")
    return mappings


def initialize(output: Path, root: Path, strict: bool, command: str) -> None:
    root = root.resolve()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "strict": strict,
        "passed": False,
        "commit": _run_git(root, "rev-parse", "HEAD").decode().strip(),
        "clean_tree": _clean_tree(root),
        "tracked_tree_sha256": tracked_tree_sha256(root),
        "release_script_sha256": _sha256_file(root / RELEASE_SCRIPT),
        "key_lock_digests": _digest_paths(root, KEY_LOCK_PATHS),
        "config_digests": _digest_paths(root, CONFIG_PATHS),
        "command": command,
        "tool_versions": {
            "python": _tool_version(sys.executable, "--version"),
            "git": _tool_version("git", "--version"),
            "node": _tool_version("node", "--version"),
            "npm": _tool_version("npm", "--version"),
            "docker": _tool_version("docker", "--version"),
            "docker_compose": _tool_version("docker", "compose", "version"),
        },
        "started_at_utc": _utc_now(),
        "ended_at_utc": None,
        "runner": _runner_identifier(),
        "evidence_sha256": {kind: {} for kind in EVIDENCE_KINDS},
    }
    _write_json(output, payload)


def _private_key_bytes(path: Path) -> bytes:
    """Read a CI key once from a no-follow descriptor beneath safe owner directories."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise AttestationError("safe private-key custody requires O_NOFOLLOW")
    lexical = Path(os.path.abspath(path))
    parts = lexical.parts
    if not parts or parts[0] != os.sep or lexical.name in {"", os.curdir, os.pardir}:
        raise AttestationError("unsafe private-key path")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptor_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    descriptors: list[int] = []
    try:
        current_fd = os.open(os.sep, directory_flags)
        descriptors.append(current_fd)
        ancestors: list[os.stat_result] = []
        for part in parts[1:-1]:
            try:
                next_fd = os.open(part, directory_flags, dir_fd=current_fd)
            except OSError as exc:
                raise AttestationError("unsafe private-key ancestor") from exc
            current_fd = next_fd
            descriptors.append(current_fd)
            metadata = os.fstat(current_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise AttestationError("unsafe private-key ancestor")
            ancestors.append(metadata)
        signer_uid = os.getuid()
        for metadata in reversed(ancestors):
            if metadata.st_uid != signer_uid:
                break
            if metadata.st_mode & 0o022:
                raise AttestationError("private-key ancestor is group/world writable")
        try:
            descriptor = os.open(parts[-1], descriptor_flags, dir_fd=current_fd)
        except OSError as exc:
            raise AttestationError("cannot open private key safely") from exc
        descriptors.append(descriptor)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != signer_uid
            or before.st_mode & 0o077
            or before.st_size > 64 * 1024
        ):
            raise AttestationError("unsafe private key ownership or permissions")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(64 * 1024 + 1)
        if len(content) > 64 * 1024 or not _same_file_identity(before, os.fstat(descriptor)):
            raise AttestationError("private key changed while being read")
        return content
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _load_private_key(path: Path) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise AttestationError("cryptography is required for signing") from exc
    try:
        key = serialization.load_pem_private_key(
            _private_key_bytes(path),
            password=None,
        )
    except AttestationError:
        raise
    except (ValueError, TypeError) as exc:
        raise AttestationError("cannot load Ed25519 private key") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise AttestationError("private key is not Ed25519")
    return key


def _private_key_outside(path: Path, root: Path, artifact_dir: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    for boundary in (root, artifact_dir):
        try:
            lexical.relative_to(boundary)
        except ValueError:
            continue
        raise AttestationError("CI private key must be outside the checkout and artifact directory")
    return lexical


def finalize(
    output: Path,
    root: Path,
    passed: bool,
    evidence: list[str],
    private_key: Path | None = None,
    signature_out: Path | None = None,
) -> None:
    payload = _read_attestation(output)
    if payload.get("ended_at_utc") is not None:
        raise AttestationError("attestation is already finalized")
    root = root.resolve()
    key_path = None
    if private_key is not None:
        if signature_out is None:
            raise AttestationError("signature output is required with a private key")
        key_path = _private_key_outside(private_key, root, output.parent.resolve())
    if passed and payload.get("strict") and (
        payload.get("clean_tree") is not True
        or not _clean_tree(root)
        or payload.get("commit") != _run_git(root, "rev-parse", "HEAD").decode().strip()
        or payload.get("tracked_tree_sha256") != tracked_tree_sha256(root)
        or payload.get("release_script_sha256") != _sha256_file(root / RELEASE_SCRIPT)
        or payload.get("key_lock_digests") != _digest_paths(root, KEY_LOCK_PATHS)
        or payload.get("config_digests") != _digest_paths(root, CONFIG_PATHS)
        or payload.get("command") != STRICT_GATE_COMMAND
    ):
        raise AttestationError("strict release inputs changed during the gate")
    if passed and payload.get("strict"):
        _worktree_matches_claimed_commit(root, payload.get("commit"))
    evidence_mapping = _evidence_mapping(output, evidence) if passed else payload["evidence_sha256"]
    if passed and payload.get("strict") and any(not evidence_mapping[kind] for kind in EVIDENCE_KINDS):
        raise AttestationError("strict attestation requires pytest, skip, pip, npm, and playwright evidence")
    signing_key = _load_private_key(key_path) if key_path is not None else None
    payload["passed"] = passed
    payload["ended_at_utc"] = _utc_now()
    payload["evidence_sha256"] = evidence_mapping
    _write_json(output, payload)
    if signing_key is not None and signature_out is not None:
        _write_atomic(
            signature_out,
            base64.b64encode(signing_key.sign(_canonical_bytes(payload))) + b"\n",
        )
    if passed and payload.get("strict"):
        _worktree_matches_claimed_commit(root, payload.get("commit"))


def _expect_mapping(value: Any, name: str) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise AttestationError(f"{name} must be a non-empty SHA-256 mapping")
    if not all(isinstance(path, str) and isinstance(digest, str) and HEX_SHA256.fullmatch(digest) for path, digest in value.items()):
        raise AttestationError(f"{name} has an invalid SHA-256 mapping")
    return value


def _verify_evidence(output: Path, evidence: Any) -> None:
    if not isinstance(evidence, dict) or set(evidence) != set(EVIDENCE_KINDS):
        raise AttestationError("evidence mappings are incomplete")
    base = output.parent.resolve()
    owners: dict[str, str] = {}
    inode_owners: dict[tuple[int, int], str] = {}
    for kind in EVIDENCE_KINDS:
        mapping = _expect_mapping(evidence[kind], f"evidence.{kind}")
        if set(mapping) != {EVIDENCE_SUMMARY_PATHS[kind]}:
            raise AttestationError("release evidence must use one canonical summary per kind")
        for relative, expected in mapping.items():
            posix = PurePosixPath(relative)
            if (
                posix.is_absolute()
                or posix.as_posix() != relative
                or "\\" in relative
                or ".." in posix.parts
            ):
                raise AttestationError("evidence paths must use canonical relative form")
            prior_kind = owners.setdefault(relative, kind)
            if prior_kind != kind:
                raise AttestationError("evidence kinds must use distinct artifact paths")
            candidate = base / relative
            _canonical, metadata = _evidence_path(candidate, base, require_file=True)
            prior_inode_kind = inode_owners.setdefault((metadata.st_dev, metadata.st_ino), kind)
            if prior_inode_kind != kind:
                raise AttestationError("evidence kinds must use distinct artifact identities")
            if _sha256_file(candidate) != expected:
                raise AttestationError(f"tampered evidence: {relative}")
            try:
                validate_gate_evidence_summary(candidate, kind)
            except ReleaseContractError as exc:
                raise AttestationError(str(exc)) from exc


def _load_public_key(path: Path) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise AttestationError("cryptography is required for verification") from exc
    try:
        key = serialization.load_pem_public_key(
            _read_stable_bytes(path, maximum=64 * 1024, label="public key")
        )
    except (ValueError, TypeError) as exc:
        raise AttestationError("cannot load trusted Ed25519 public key") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise AttestationError("trusted public key is not Ed25519")
    return key


def verify(output: Path, root: Path, public_key: Path, signature: Path) -> None:
    payload = _read_attestation(output)
    required = {
        "schema_version", "strict", "passed", "commit", "clean_tree", "tracked_tree_sha256",
        "release_script_sha256", "key_lock_digests", "config_digests", "command", "tool_versions",
        "started_at_utc", "ended_at_utc", "runner", "evidence_sha256",
    }
    if set(payload) != required or payload["schema_version"] != SCHEMA_VERSION:
        raise AttestationError("unsupported or malformed attestation schema")
    if payload["strict"] is not True or payload["passed"] is not True or payload["clean_tree"] is not True:
        raise AttestationError("release verification requires a strict, passed, clean attestation")
    if not isinstance(payload["commit"], str) or not COMMIT.fullmatch(payload["commit"]):
        raise AttestationError("attestation commit is malformed")
    if not isinstance(payload["tracked_tree_sha256"], str) or not HEX_SHA256.fullmatch(payload["tracked_tree_sha256"]):
        raise AttestationError("attestation tracked tree digest is malformed")
    if not isinstance(payload["release_script_sha256"], str) or not HEX_SHA256.fullmatch(payload["release_script_sha256"]):
        raise AttestationError("release script digest is malformed")
    if payload.get("command") != STRICT_GATE_COMMAND:
        raise AttestationError("attestation command is malformed")
    if not isinstance(payload["runner"], str) or not payload["runner"] or RUNNER_SAFE.search(payload["runner"]):
        raise AttestationError("attestation runner identifier is malformed")
    if not isinstance(payload["started_at_utc"], str) or not isinstance(payload["ended_at_utc"], str):
        raise AttestationError("attestation timestamps are malformed")
    if not isinstance(payload["tool_versions"], dict) or set(payload["tool_versions"]) != TOOL_VERSION_KEYS or not all(
        isinstance(name, str) and isinstance(version, str) for name, version in payload["tool_versions"].items()
    ):
        raise AttestationError("attestation tool versions are malformed")
    _expect_mapping(payload["key_lock_digests"], "key lock digests")
    _expect_mapping(payload["config_digests"], "configuration digests")
    root = root.resolve()
    if not _clean_tree(root):
        raise AttestationError("checked-out tree is dirty")
    if payload["commit"] != _run_git(root, "rev-parse", "HEAD").decode().strip():
        raise AttestationError("attestation commit does not match the checked-out commit")
    if payload["tracked_tree_sha256"] != tracked_tree_sha256(root):
        raise AttestationError("attestation tracked tree does not match the checked-out tree")
    if payload["release_script_sha256"] != _sha256_file(root / RELEASE_SCRIPT):
        raise AttestationError("release script digest does not match")
    if payload["key_lock_digests"] != _digest_paths(root, KEY_LOCK_PATHS):
        raise AttestationError("key lock digests do not match")
    if payload["config_digests"] != _digest_paths(root, CONFIG_PATHS):
        raise AttestationError("configuration digests do not match")
    _worktree_matches_claimed_commit(root, payload.get("commit"))
    _verify_evidence(output, payload["evidence_sha256"])
    try:
        signature_bytes = _read_stable_bytes(
            signature,
            maximum=64 * 1024,
            label="detached signature",
        )
        decoded = base64.b64decode(signature_bytes.strip(), validate=True)
        if signature_bytes != base64.b64encode(decoded) + b"\n":
            raise ValueError("noncanonical detached signature")
        _load_public_key(public_key).verify(decoded, _canonical_bytes(payload))
    except (OSError, ValueError) as exc:
        raise AttestationError("missing or malformed detached signature") from exc
    except Exception as exc:
        if exc.__class__.__name__ == "InvalidSignature":
            raise AttestationError("detached signature does not verify") from exc
        raise


def sign_finalized(output: Path, root: Path, private_key: Path, signature: Path) -> None:
    """Sign only a finalized strict attestation in a separate trusted signing job."""
    payload = _read_attestation(output)
    if (
        payload.get("strict") is not True
        or payload.get("passed") is not True
        or payload.get("clean_tree") is not True
        or not isinstance(payload.get("ended_at_utc"), str)
        or payload.get("command") != STRICT_GATE_COMMAND
    ):
        raise AttestationError("only a finalized strict, passed, clean attestation can be signed")
    root = root.resolve()
    if (
        not _clean_tree(root)
        or payload.get("commit") != _run_git(root, "rev-parse", "HEAD").decode().strip()
        or payload.get("tracked_tree_sha256") != tracked_tree_sha256(root)
        or payload.get("release_script_sha256") != _sha256_file(root / RELEASE_SCRIPT)
        or payload.get("key_lock_digests") != _digest_paths(root, KEY_LOCK_PATHS)
        or payload.get("config_digests") != _digest_paths(root, CONFIG_PATHS)
    ):
        raise AttestationError("finalized attestation no longer matches the clean checkout")
    _worktree_matches_claimed_commit(root, payload.get("commit"))
    _verify_evidence(output, payload.get("evidence_sha256"))
    key_path = _private_key_outside(private_key, root, output.parent.resolve())
    _write_atomic(
        signature,
        base64.b64encode(_load_private_key(key_path).sign(_canonical_bytes(payload))) + b"\n",
    )
    _worktree_matches_claimed_commit(root, payload.get("commit"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify a release gate attestation")
    commands = parser.add_subparsers(dest="action", required=True)
    initialize_parser = commands.add_parser("initialize")
    initialize_parser.add_argument("--output", required=True, type=Path)
    initialize_parser.add_argument("--root", required=True, type=Path)
    initialize_parser.add_argument("--strict", action="store_true")
    initialize_parser.add_argument("--command", required=True)
    finalize_parser = commands.add_parser("finalize")
    finalize_parser.add_argument("--output", required=True, type=Path)
    finalize_parser.add_argument("--root", required=True, type=Path)
    outcome = finalize_parser.add_mutually_exclusive_group(required=True)
    outcome.add_argument("--passed", action="store_true")
    outcome.add_argument("--failed", action="store_true")
    finalize_parser.add_argument("--evidence", action="append", default=[])
    finalize_parser.add_argument("--private-key", type=Path)
    finalize_parser.add_argument("--signature-out", type=Path)
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--attestation", required=True, type=Path)
    verify_parser.add_argument("--root", required=True, type=Path)
    verify_parser.add_argument("--public-key", required=True, type=Path)
    verify_parser.add_argument("--signature", type=Path)
    sign_parser = commands.add_parser("sign")
    sign_parser.add_argument("--attestation", required=True, type=Path)
    sign_parser.add_argument("--root", required=True, type=Path)
    sign_parser.add_argument("--private-key", required=True, type=Path)
    sign_parser.add_argument("--signature-out", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "initialize":
            initialize(args.output, args.root, args.strict, args.command)
        elif args.action == "finalize":
            finalize(
                args.output, args.root, args.passed, args.evidence, args.private_key, args.signature_out
            )
        elif args.action == "verify":
            verify(args.attestation, args.root, args.public_key, args.signature or args.attestation.with_suffix(args.attestation.suffix + ".sig"))
        else:
            sign_finalized(args.attestation, args.root, args.private_key, args.signature_out)
    except AttestationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
