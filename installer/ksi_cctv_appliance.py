#!/usr/bin/env python3
"""Offline appliance operator for KSI-CCTV.

This tool deliberately does not elevate privileges. Install it with the
required ownership and invoke it as an account permitted to manage the unit.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import hashlib
import ipaddress
import json
import os
import platform
import re
import secrets
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from release_contracts import (
    CONTRACT_PATH,
    ReleaseContractError,
    ed25519_public_bytes,
    load_external_ed25519_public_key,
    load_release_contract,
    persistent_contract_compatible,
    read_stable_file_bytes,
    validate_release_contract,
    validate_single_image_archive,
)
from verify_systemd_unit import validate_unit

SERVICE = "ksi-cctv-compose.service"
PROFILES = {"dgx-spark-arm64"}
RELEASE_SCHEMA_VERSION = 3
STATE_SCHEMA_VERSION = 1
ACTIVATION_HEALTH_COMMAND = ("/usr/bin/python3", "/opt/ksi-cctv/current/bin/activation_health.py")
SECRET_PATTERN = re.compile(
    r"(?i)((?:password|secret|token|api[_-]?key|capture[_-]?key)\s*[=:]\s*)([^\s'\"]+)"
)
VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MAX_BACKUP_MEMBERS = 10_000
MAX_BACKUP_UNCOMPRESSED_BYTES = 10 * 1024 * 1024 * 1024
BACKUP_MAGIC = b"KSI-CCTV-BACKUP-1\n"
BACKUP_CHUNK_BYTES = 1024 * 1024
DEFAULT_HEALTH_TIMEOUT = 60
TRANSACTION_AUTHORIZATION_NAME = "ksi-cctv-transaction-start-authorized"
RUNTIME_UID = 1000
RUNTIME_GID = 1000
REQUIRED_SITE_ENV = {
    "KSI_CCTV_API_KEY",
    "KSI_CCTV_SITE_ADDRESS",
    "KSI_CCTV_WEB_PASSWORD",
    "KSI_CCTV_WEB_SECRET",
    "KSI_CCTV_CAPTURE_KEY",
}
DISTINCT_SECRET_KEYS = {
    "KSI_CCTV_API_KEY",
    "KSI_CCTV_WEB_PASSWORD",
    "KSI_CCTV_WEB_SECRET",
    "KSI_CCTV_CAPTURE_KEY",
}
FORBIDDEN_PRODUCTION_ENV = {
    "KSI_CCTV_ALLOW_LOOPBACK_CAMERA",
    "KSI_CCTV_DEV_AUTH_BYPASS",
    "KSI_CCTV_WEB_DEV",
}
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
MANIFEST_FIELDS = {
    "schema_version", "state_schema", "version", "hardware_profiles", "files",
    "artifacts", "images", "source", "sboms", "third_party_notices",
    "model_inventory", "license_approvals", "persistent_state", "healthcheck",
}


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: yaml.SafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark, f"duplicate key: {key}", key_node.start_mark
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)
MEDIAMTX_CONTRACT = {
    "logLevel": "info",
    "logDestinations": ["stdout"],
    "rtsp": True,
    "rtspAddress": ":8554",
    "rtspTransports": ["tcp"],
    "hls": True,
    "hlsAddress": ":8888",
    "hlsVariant": "mpegts",
    "hlsAlwaysRemux": False,
    "webrtc": False,
    "rtmp": False,
    "srt": False,
    "api": True,
    "apiAddress": ":9997",
    "pathDefaults": {"source": "publisher"},
    "paths": {"all_others": None},
}


class ApplianceError(RuntimeError):
    pass


class PersistentStateUnsafeError(ApplianceError):
    """Candidate persistent state changed or could not be compared safely."""


class ImageCleanupUnsafeError(ApplianceError):
    """Docker mutation could not be completely reconciled."""


def caused_by(error: BaseException, kind: type[BaseException]) -> bool:
    current: BaseException | None = error
    while current is not None:
        if isinstance(current, kind):
            return True
        current = current.__cause__ or current.__context__
    return False


def mask_secrets(value: str) -> str:
    return SECRET_PATTERN.sub(r"\1***", value)


def valid_fernet_key(value: str) -> bool:
    """Validate a Fernet-compatible key without importing application dependencies."""
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
    except (UnicodeEncodeError, ValueError):
        return False
    return len(decoded) == 32 and base64.urlsafe_b64encode(decoded).decode("ascii") == value


def write_json_0600(path: Path, value: dict[str, Any]) -> None:
    write_bytes_0600(
        path,
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"),
    )


def write_bytes_0600(path: Path, value: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def clear_durable(path: Path) -> None:
    path.unlink(missing_ok=True)
    if path.parent.is_dir():
        fsync_directory(path.parent)


def fsync_directory(path: Path) -> None:
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def durable_remove_tree(path: Path) -> None:
    """Remove a private staging tree and durably commit its disappearance."""
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
    if path.parent.is_dir():
        fsync_directory(path.parent)


def ensure_directory(path: Path, mode: int) -> None:
    if path.is_symlink():
        raise ApplianceError(f"appliance directory must not be a symlink: {path}")
    created = not path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ApplianceError(f"cannot safely open appliance directory: {path}") from exc
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ApplianceError(f"appliance path is not a directory: {path}")
        os.fchmod(fd, mode)
        os.fsync(fd)
    finally:
        os.close(fd)
    if created and path.parent.is_dir():
        fsync_directory(path.parent)


class Layout:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.opt = self.root / "opt" / "ksi-cctv"
        self.etc = self.root / "etc" / "ksi-cctv"
        self.data = self.root / "var" / "lib" / "ksi-cctv"
        self.log = self.root / "var" / "log" / "ksi-cctv"
        self.backups = self.root / "var" / "backups" / "ksi-cctv"
        self.releases = self.opt / "releases"
        self.current = self.opt / "current"
        self.rollback = self.etc / "rollback.json"
        self.transaction = self.opt / "transaction.json"
        self.sensitive_staging = self.opt / "sensitive-staging.json"
        self.backup_identity = self.opt / "backup-identity"
        self.appliance_id = self.backup_identity / "appliance-id"
        self.backup_signing_private = self.backup_identity / "backup-signing.ed25519"
        self.backup_signing_public = self.backup_identity / "backup-signing.ed25519.pub"
        self.backup_sequence = self.backup_identity / "backup-sequence.json"
        self.last_restore_sequence = self.backup_identity / "last-restore-sequence.json"
        self.operation_lock = self.root / "run" / "lock" / "ksi-cctv-appliance.lock"
        self.start_authorization = self.root / "run" / "ksi-cctv" / TRANSACTION_AUTHORIZATION_NAME
        self.backup_recipient = self.etc / "backup-recipient.pub"
        self.site_environment = self.etc / "ksi-cctv.env"
        self.engine_environment = self.etc / "engine.env"
        self.web_environment = self.etc / "web.env"
        self.trust_roots = self.etc / "trust-roots"
        self.release_key = self.trust_roots / "release-signing.pub"
        self.attestation_key = self.trust_roots / "ci-attestation.pub"
        self.config = self.data / "config"
        self.cameras = self.data / "cameras"
        self.outbox = self.data / "outbox"
        self.captures = self.data / "captures"
        self.events = self.data / "events"
        self.models = self.data / "models"


def write_transaction(layout: Layout, value: dict[str, Any], phase: str) -> None:
    write_json_0600(layout.transaction, {**value, "phase": phase})


def load_transaction(layout: Layout) -> dict[str, Any] | None:
    if layout.transaction.is_symlink():
        raise ApplianceError("appliance transaction journal path is unsafe; manual recovery is required")
    if not layout.transaction.exists():
        return None
    if not layout.transaction.is_file():
        raise ApplianceError("appliance transaction journal is not a regular file; manual recovery is required")
    try:
        value = json.loads(layout.transaction.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplianceError("appliance transaction journal is unreadable; manual recovery is required") from exc
    if not isinstance(value, dict) or value.get("operation") not in {
        "backup", "install", "upgrade", "rollback", "restore"
    }:
        raise ApplianceError("appliance transaction journal is invalid; manual recovery is required")
    return value


def _validated_sensitive_path(layout: Layout, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ApplianceError("sensitive staging journal contains an invalid path")
    path = Path(value)
    if not path.is_absolute():
        raise ApplianceError("sensitive staging path must be absolute")
    backup_parent = layout.backups
    restore_parent = layout.root / "var" / "tmp"
    valid_backup = path.parent == backup_parent and path.name.startswith(
        (".ksi-cctv-", "ksi-cctv-backup-")
    )
    valid_restore = path.parent == restore_parent and path.name.startswith(
        ("ksi-cctv-backup-decrypt-", "ksi-cctv-restore-")
    )
    if not valid_backup and not valid_restore:
        raise ApplianceError("sensitive staging path is outside the approved private staging areas")
    return path


def load_sensitive_staging(layout: Layout) -> list[Path]:
    if layout.sensitive_staging.is_symlink():
        raise ApplianceError("sensitive staging journal path is unsafe; manual recovery is required")
    if not layout.sensitive_staging.exists():
        return []
    if not layout.sensitive_staging.is_file():
        raise ApplianceError("sensitive staging journal is not a regular file; manual recovery is required")
    try:
        value = json.loads(layout.sensitive_staging.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplianceError("sensitive staging journal is unreadable; manual recovery is required") from exc
    paths = value.get("paths") if isinstance(value, dict) and value.get("schema_version") == 1 else None
    if not isinstance(paths, list) or not paths:
        raise ApplianceError("sensitive staging journal is invalid; manual recovery is required")
    result = [_validated_sensitive_path(layout, item) for item in paths]
    if len(set(result)) != len(result):
        raise ApplianceError("sensitive staging journal contains duplicate paths")
    return result


def register_sensitive_staging(layout: Layout, *paths: Path) -> None:
    ensure_directory(layout.opt, 0o755)
    current = load_sensitive_staging(layout)
    additions = [_validated_sensitive_path(layout, str(path)) for path in paths]
    if any(path.exists() or path.is_symlink() for path in additions):
        raise ApplianceError("sensitive staging path already exists")
    combined = [*current, *(path for path in additions if path not in current)]
    write_json_0600(
        layout.sensitive_staging,
        {"schema_version": 1, "paths": [str(path) for path in combined]},
    )


def unregister_sensitive_staging(layout: Layout, *paths: Path) -> None:
    current = load_sensitive_staging(layout)
    removals = {_validated_sensitive_path(layout, str(path)) for path in paths}
    remaining = [path for path in current if path not in removals]
    if remaining:
        write_json_0600(
            layout.sensitive_staging,
            {"schema_version": 1, "paths": [str(path) for path in remaining]},
        )
    else:
        clear_durable(layout.sensitive_staging)


def cleanup_sensitive_staging(layout: Layout) -> bool:
    paths = load_sensitive_staging(layout)
    for path in paths:
        if path.is_dir() and not path.is_symlink():
            durable_remove_tree(path)
        else:
            clear_durable(path)
    if paths:
        clear_durable(layout.sensitive_staging)
    return bool(paths)


@contextlib.contextmanager
def appliance_operation_lock(layout: Layout) -> Iterator[None]:
    try:
        layout.operation_lock.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        fd = os.open(layout.operation_lock, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise ApplianceError("cannot acquire the Appliance operation lock") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ApplianceError("another Appliance operation is already in progress") from exc
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def persistent_state_fingerprint(layout: Layout) -> str:
    """Hash persistent content and security metadata while the service is stopped."""
    digest = hashlib.sha256()
    if not layout.data.exists():
        return digest.hexdigest()
    for path in (layout.data, *sorted(layout.data.rglob("*"))):
        relative = "." if path == layout.data else path.relative_to(layout.data).as_posix()
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise ApplianceError(f"persistent state cannot be inspected: {relative}") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise ApplianceError(f"persistent state must not contain symlinks: {relative}")
        if stat.S_ISREG(metadata.st_mode):
            if metadata.st_nlink != 1:
                raise ApplianceError(f"persistent state file must have one link: {relative}")
            entry_type = "file"
        elif stat.S_ISDIR(metadata.st_mode):
            entry_type = "directory"
        else:
            raise ApplianceError(f"persistent state contains an unsupported entry: {relative}")
        try:
            xattrs = {
                name: base64.b64encode(os.getxattr(path, name, follow_symlinks=False)).decode("ascii")
                for name in sorted(os.listxattr(path, follow_symlinks=False))
            }
        except OSError as exc:
            raise ApplianceError(f"persistent state metadata cannot be read: {relative}") from exc
        descriptor = {
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
            "path": relative,
            "type": entry_type,
            "uid": metadata.st_uid,
            "xattrs": xattrs,
        }
        digest.update(canonical_manifest(descriptor))
        digest.update(b"\0")
        if entry_type == "file":
            digest.update(sha256(path).encode("ascii"))
            digest.update(b"\0")
    return digest.hexdigest()


def load_manifest(release: Path) -> dict[str, Any]:
    path = release / "release-manifest.json"

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ApplianceError(f"release manifest contains a duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        raw = read_stable_file_bytes(path, maximum=16 * 1024 * 1024)
        manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError, ReleaseContractError) as exc:
        raise ApplianceError(f"invalid release manifest: {path}") from exc
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_FIELDS:
        raise ApplianceError("release manifest fields are incomplete or unexpected")
    if raw != canonical_manifest(manifest):
        raise ApplianceError("release manifest must use canonical JSON bytes")
    if manifest.get("schema_version") != RELEASE_SCHEMA_VERSION:
        raise ApplianceError("unsupported release manifest schema")
    if manifest.get("state_schema") != STATE_SCHEMA_VERSION:
        raise ApplianceError("unsupported appliance state schema")
    if not isinstance(manifest.get("version"), str) or not VERSION_PATTERN.fullmatch(manifest["version"]):
        raise ApplianceError("manifest requires a safe release version")
    for field in ("files", "artifacts"):
        mapping = manifest.get(field)
        if not isinstance(mapping, dict) or not mapping or any(
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or re.fullmatch(r"[A-Fa-f0-9]{64}", digest) is None
            for name, digest in mapping.items()
        ):
            raise ApplianceError(f"manifest requires non-empty SHA-256 {field}")
    profiles = manifest.get("hardware_profiles", [])
    if (
        not isinstance(profiles, list)
        or not profiles
        or any(not isinstance(profile, str) for profile in profiles)
        or len(profiles) != len(set(profiles))
        or not set(profiles).issubset(PROFILES)
    ):
        raise ApplianceError("manifest has unsupported hardware profiles")
    images = manifest.get("images")
    if not isinstance(images, dict) or len(images) != 4:
        raise ApplianceError("manifest requires exactly four offline image mappings")
    return manifest


def canonical_manifest(manifest: dict[str, Any]) -> bytes:
    return json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def external_ed25519_key_material(release: Path, path: Path, label: str) -> tuple[Any, bytes]:
    try:
        from cryptography.hazmat.primitives import serialization

        key = load_external_ed25519_public_key(release, path)
        raw = key.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
    except (ImportError, ReleaseContractError) as exc:
        raise ApplianceError(f"external {label} public key is invalid or unsafe") from exc
    return key, base64.b64encode(raw) + b"\n"


def verify_signature(
    release: Path,
    manifest: dict[str, Any],
    public_key: Path | None,
    allow_unsigned: bool,
    *,
    public_key_object: Any | None = None,
) -> None:
    signature_path = release / "release-manifest.sig"
    if not signature_path.is_file():
        if allow_unsigned:
            return
        raise ApplianceError("release signature is required; --allow-unsigned is unsafe and for development only")
    if public_key_object is None and (public_key is None or not public_key.is_file()):
        raise ApplianceError("trusted release public key is required outside the release")
    try:
        from cryptography.exceptions import InvalidSignature
    except ImportError as exc:
        raise ApplianceError("cryptography with Ed25519 support is required for release verification") from exc
    try:
        if public_key_object is None:
            assert public_key is not None
            key = load_external_ed25519_public_key(release, public_key)
        else:
            key = public_key_object
        signature_bytes = read_stable_file_bytes(signature_path, maximum=1024)
        signature = base64.b64decode(signature_bytes, validate=True)
        if signature_bytes != base64.b64encode(signature):
            raise ValueError("noncanonical release signature")
        key.verify(signature, canonical_manifest(manifest))
    except ReleaseContractError as exc:
        raise ApplianceError(str(exc)) from exc
    except (OSError, ValueError, InvalidSignature) as exc:
        raise ApplianceError("release signature verification failed") from exc


def _compose_document(path: Path) -> dict[str, Any]:
    try:
        value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ApplianceError(f"invalid Appliance Compose YAML: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise ApplianceError("Appliance Compose must be a YAML object")
    return value


def _typed_equal(actual: Any, expected: Any) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(actual) == set(expected) and all(
            _typed_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(
            _typed_equal(item, expected_item) for item, expected_item in zip(actual, expected, strict=True)
        )
    return bool(actual == expected)


def compose_service_images(release: Path) -> dict[str, str]:
    document = _compose_document(release / "compose.yaml")
    services = document.get("services")
    if not isinstance(services, dict):
        raise ApplianceError("Appliance Compose services must be an object")
    result: dict[str, str] = {}
    for name, service in services.items():
        if not isinstance(name, str) or not isinstance(service, dict) or not isinstance(service.get("image"), str):
            raise ApplianceError("every Appliance Compose service requires a string image")
        result[name] = service["image"]
    return result


def compose_images(release: Path) -> list[str]:
    images = list(compose_service_images(release).values())
    for image in images:
        if (
            "REPLACE_" in image
            or "PLACEHOLDER" in image
            or image.endswith(":latest")
            or re.fullmatch(r"sha256:[a-f0-9]{64}", image) is None
        ):
            raise ApplianceError(f"compose image is not pinned: {image}")
    return images


def validate_appliance_topology(release: Path) -> None:
    compose = (release / "compose.yaml").read_text(encoding="utf-8")
    if re.search(r"(?m)(?:^|\s)(?:<<:|&[A-Za-z]|\*[A-Za-z])", compose):
        raise ApplianceError("appliance compose must not use YAML anchors, aliases, or merge keys")
    if "/var/run/docker.sock" in compose or "/run/docker.sock" in compose:
        raise ApplianceError("appliance compose must not mount the Docker control socket")
    actual = _compose_document(release / "compose.yaml")
    template_path = Path(__file__).with_name("compose.appliance.yaml.template")
    template = template_path.read_text(encoding="utf-8")
    template = re.sub(r"REPLACE_[A-Z0-9_]+", "0" * 64, template)
    expected = yaml.load(template, Loader=UniqueKeyLoader)
    if set(actual) != {"services", "networks"} or not isinstance(expected, dict):
        raise ApplianceError("appliance compose top-level fields are invalid")
    actual_services = actual.get("services")
    expected_services = expected.get("services")
    if not isinstance(actual_services, dict) or not isinstance(expected_services, dict):
        raise ApplianceError("appliance compose services are invalid")
    if set(actual_services) != set(expected_services):
        raise ApplianceError("appliance compose service set is invalid")
    for name, expected_service in expected_services.items():
        actual_service = actual_services.get(name)
        if not isinstance(actual_service, dict) or not isinstance(expected_service, dict):
            raise ApplianceError(f"appliance service is invalid: {name}")
        actual_contract = {key: value for key, value in actual_service.items() if key != "image"}
        expected_contract = {key: value for key, value in expected_service.items() if key != "image"}
        if not _typed_equal(actual_contract, expected_contract):
            raise ApplianceError(f"appliance service contract is invalid: {name}")
    if not _typed_equal(actual.get("networks"), expected.get("networks")):
        raise ApplianceError("appliance compose network contract is invalid")


def validate_compose(release: Path) -> None:
    compose_images(release)
    validate_appliance_topology(release)


def required_release_files(release: Path) -> set[str]:
    return {
        "bin/activation_health.py",
        "bin/transaction_guard.py",
        f"systemd/{SERVICE}",
        "Caddyfile",
        "mediamtx.yml",
    }


def validate_host_runtime_contracts(release: Path) -> None:
    unit_errors = validate_unit(release / "systemd" / SERVICE)
    if unit_errors:
        raise ApplianceError(f"signed systemd unit violates the Appliance contract: {unit_errors[0]}")
    caddy = release / "Caddyfile"
    expected_caddy = Path(__file__).with_name("Caddyfile")
    if not caddy.is_file() or caddy.read_bytes() != expected_caddy.read_bytes():
        raise ApplianceError("signed Caddyfile does not match the Appliance security contract")
    try:
        mediamtx = yaml.load((release / "mediamtx.yml").read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ApplianceError("signed MediaMTX configuration is invalid") from exc
    if not _typed_equal(mediamtx, MEDIAMTX_CONTRACT):
        raise ApplianceError("signed MediaMTX configuration violates the HLS-only contract")
    for relative, canonical in (
        ("bin/activation_health.py", Path(__file__).with_name("activation_health.py")),
        ("bin/transaction_guard.py", Path(__file__).with_name("transaction_guard.py")),
    ):
        candidate = release / relative
        if not candidate.is_file() or candidate.read_bytes() != canonical.read_bytes():
            raise ApplianceError(f"signed host runtime script does not match gated source: {relative}")


def healthcheck_command(manifest: dict[str, Any]) -> list[str]:
    health = manifest.get("healthcheck")
    if (
        not isinstance(health, dict)
        or set(health) != {"command", "timeout_seconds"}
        or not isinstance(health.get("command"), list)
        or not health["command"]
    ):
        raise ApplianceError("manifest requires a non-empty healthcheck command")
    if not all(isinstance(item, str) and item for item in health["command"]):
        raise ApplianceError("healthcheck command must contain non-empty strings")
    if "timeout_seconds" in health and (
        not isinstance(health["timeout_seconds"], int) or health["timeout_seconds"] < 1
    ):
        raise ApplianceError("healthcheck timeout_seconds must be a positive integer")
    if tuple(health["command"]) != ACTIVATION_HEALTH_COMMAND:
        raise ApplianceError("healthcheck command must use the signed activation health contract")
    return health["command"]


def verify_release(
    release: Path,
    selected_profile: str | None = None,
    public_key: Path | None = None,
    allow_unsigned: bool = False,
    attestation_public_key: Path | None = None,
    *,
    release_key_object: Any | None = None,
    attestation_key_object: Any | None = None,
) -> dict[str, Any]:
    if not release.is_dir():
        raise ApplianceError(f"release directory does not exist: {release}")
    manifest = load_manifest(release)
    if selected_profile and selected_profile not in manifest.get("hardware_profiles", []):
        raise ApplianceError(f"release does not support hardware profile: {selected_profile}")
    actual_files: set[str] = set()
    for candidate in release.rglob("*"):
        if candidate.is_symlink():
            raise ApplianceError(f"release contains a symlink: {candidate.relative_to(release)}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise ApplianceError(
                f"release contains a special filesystem entry: {candidate.relative_to(release)}"
            )
        relative_name = candidate.relative_to(release).as_posix()
        if relative_name not in {"release-manifest.json", "release-manifest.sig"}:
            actual_files.add(relative_name)
    if actual_files != set(manifest["files"]):
        raise ApplianceError("release payload files do not exactly match the signed manifest")
    missing_runtime = required_release_files(release) - actual_files
    if missing_runtime:
        raise ApplianceError(
            f"release is missing required runtime files: {', '.join(sorted(missing_runtime))}"
        )
    validate_host_runtime_contracts(release)
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts or not isinstance(expected, str):
            raise ApplianceError(f"unsafe manifest entry: {name}")
        candidate = release / relative
        if candidate.is_symlink() or not candidate.is_file() or sha256(candidate).lower() != expected.lower():
            raise ApplianceError(f"checksum verification failed: {name}")
    artifacts = manifest.get("artifacts", {})
    if not isinstance(artifacts, dict):
        raise ApplianceError("manifest artifacts must be a checksum mapping")
    for name, expected in artifacts.items():
        if name not in manifest["files"] or manifest["files"][name].lower() != str(expected).lower():
            raise ApplianceError(f"artifact checksum is not declared in files: {name}")
    if "compose.yaml" not in manifest["files"]:
        raise ApplianceError("manifest must declare compose.yaml")
    validate_compose(release)
    images = manifest.get("images")
    if not isinstance(images, dict) or set(images) != set(compose_images(release)):
        raise ApplianceError("manifest images must map every Compose image exactly")
    for image, name in images.items():
        if not isinstance(name, str) or not name.startswith("images/") or not name.endswith(".tar"):
            raise ApplianceError(f"invalid offline archive for image: {image}")
        if name not in artifacts or name not in manifest["files"]:
            raise ApplianceError(f"image archive is not checksum-declared: {name}")
    try:
        if release_key_object is None and public_key is not None:
            release_key_object = load_external_ed25519_public_key(release, public_key)
        if attestation_key_object is None and attestation_public_key is not None:
            attestation_key_object = load_external_ed25519_public_key(release, attestation_public_key)
        contract = load_release_contract(release)
        for key in (
            "source", "sboms", "third_party_notices", "model_inventory",
            "license_approvals", "persistent_state",
        ):
            if manifest.get(key) != contract.get(key):
                raise ReleaseContractError(f"manifest and {CONTRACT_PATH} disagree on {key}")
        validate_release_contract(
            release,
            contract,
            set(images),
            manifest["files"],
            version=manifest["version"],
            profiles=manifest["hardware_profiles"],
            image_archives=images,
            engine_image=compose_service_images(release)["engine"],
            trusted_attestation_key_object=attestation_key_object,
            allow_untrusted_attestation=allow_unsigned,
        )
    except ReleaseContractError as exc:
        raise ApplianceError(str(exc)) from exc
    healthcheck_command(manifest)
    verify_signature(
        release,
        manifest,
        public_key,
        allow_unsigned,
        public_key_object=release_key_object,
    )
    if (
        not allow_unsigned
        and public_key is not None
        and attestation_public_key is not None
        and release_key_object is not None
        and attestation_key_object is not None
    ):
        from cryptography.hazmat.primitives import serialization

        if release_key_object.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ) == attestation_key_object.public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        ):
            raise ApplianceError("release-signing and CI-attestation keys must be distinct")
    return manifest


def selected_hardware_profile(profile: str | None) -> str | None:
    if profile:
        if profile not in PROFILES:
            raise ApplianceError(f"unsupported hardware profile: {profile}")
        return profile
    return None


def validate_hardware(profile: str) -> list[str]:
    issues: list[str] = []
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        issues.append("requires an ARM64 host")
    model = ""
    with contextlib.suppress(OSError):
        model = Path("/proc/device-tree/model").read_text(encoding="utf-8", errors="ignore").lower()
    if profile == "jetson-arm64" and model and "jetson" not in model:
        issues.append("host model is not identified as NVIDIA Jetson")
    if profile == "dgx-spark-arm64" and model and "dgx" not in model and "spark" not in model:
        issues.append("host model is not identified as DGX Spark")
    return issues


def run_command(command: Sequence[str], timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(list(command), check=False, text=True, capture_output=True, shell=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ApplianceError(f"command timed out after {timeout} seconds: {command[0]}") from exc
    except FileNotFoundError as exc:
        raise ApplianceError(f"command is unavailable: {command[0]}") from exc
    result.stdout = mask_secrets(result.stdout or "")
    result.stderr = mask_secrets(result.stderr or "")
    return result


HOST_RUN_COMMAND = run_command


def require_confirmation(args: argparse.Namespace) -> None:
    if not args.confirm:
        raise ApplianceError("this action changes appliance state; re-run with --confirm")


def atomic_switch(link: Path, target: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    temporary = link.with_name(f".{link.name}.{os.getpid()}.new")
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, link)
    directory_fd = os.open(link.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def current_release(layout: Layout) -> Path | None:
    if not layout.current.is_symlink():
        return None
    target = layout.current.resolve()
    try:
        target.relative_to(layout.releases.resolve())
    except ValueError as exc:
        raise ApplianceError("current release points outside the releases directory") from exc
    return target


def _parse_backup_public_key(data: bytes):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

        key = serialization.load_pem_public_key(data)
    except (ImportError, ValueError) as exc:
        raise ApplianceError("a PEM RSA backup recipient public key is required") from exc
    if not isinstance(key, RSAPublicKey) or key.key_size < 3072:
        raise ApplianceError("backup recipient key must be an RSA public key of at least 3072 bits")
    return key


def _backup_public_key_material(path: Path) -> tuple[Any, bytes]:
    from cryptography.hazmat.primitives import serialization

    try:
        data = read_stable_file_bytes(
            path,
            maximum=64 * 1024,
            owner_uid=os.geteuid(),
            reject_writable_by_others=True,
            safe_ancestors=True,
        )
    except ReleaseContractError as exc:
        raise ApplianceError("backup recipient public key has unsafe custody") from exc
    key = _parse_backup_public_key(data)
    canonical = key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return key, canonical


def _load_backup_public_key(path: Path):
    return _backup_public_key_material(path)[0]


def _load_backup_private_key(path: Path):
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

        data = read_stable_file_bytes(
            path,
            maximum=64 * 1024,
            owner_uid=os.geteuid(),
            owner_only=True,
            safe_ancestors=True,
        )
        key = serialization.load_pem_private_key(data, password=None)
    except ReleaseContractError as exc:
        raise ApplianceError(
            "backup recipient private key must be an owner-only regular file owned by the operator"
        ) from exc
    except (ImportError, ValueError, TypeError) as exc:
        raise ApplianceError("an unencrypted PEM RSA backup recipient private key is required") from exc
    if not isinstance(key, RSAPrivateKey) or key.key_size < 3072:
        raise ApplianceError("backup recipient key must be an RSA private key of at least 3072 bits")
    return key


def _backup_manifest(staging: Path) -> dict[str, Any]:
    entries: dict[str, dict[str, Any]] = {}
    roots = ("etc/ksi-cctv", "var/lib/ksi-cctv", "var/log/ksi-cctv")
    for root in roots:
        source = staging / root
        if not source.is_dir() or source.is_symlink():
            raise ApplianceError(f"backup snapshot is missing required root: {root}")
        for path in (source, *sorted(source.rglob("*"))):
            relative = path.relative_to(staging).as_posix()
            if path.is_symlink():
                raise ApplianceError(f"backup snapshot contains a symlink: {relative}")
            mode = stat.S_IMODE(path.stat().st_mode) & 0o777
            if path.is_dir():
                entries[relative] = {"mode": mode, "type": "directory"}
            elif path.is_file():
                entries[relative] = {
                    "mode": mode,
                    "sha256": sha256(path),
                    "size": path.stat().st_size,
                    "type": "file",
                }
            else:
                raise ApplianceError(f"backup snapshot contains a special file: {relative}")
    return {"schema_version": 1, "roots": list(roots), "entries": entries}


def install_backup_recipient(
    layout: Layout,
    supplied_key: str | None,
    *,
    verified_bytes: bytes | None = None,
) -> None:
    if not supplied_key:
        raise ApplianceError("install requires --backup-recipient-public-key for encrypted backups")
    if verified_bytes is None:
        _key, verified_bytes = _backup_public_key_material(Path(supplied_key))
    else:
        _parse_backup_public_key(verified_bytes)
    layout.etc.mkdir(parents=True, exist_ok=True)
    write_bytes_0600(layout.backup_recipient, verified_bytes)
    _persisted_key, persisted = _backup_public_key_material(layout.backup_recipient)
    if persisted != verified_bytes:
        raise ApplianceError("persisted backup recipient key does not match the verified key")


def _read_sequence(path: Path, label: str) -> int:
    if path.is_symlink() or not path.is_file():
        raise ApplianceError(f"{label} sequence state is missing or unsafe")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplianceError(f"{label} sequence state is unreadable") from exc
    if not isinstance(value, dict) or set(value) != {"sequence"} or not isinstance(value["sequence"], int) or value["sequence"] < 0:
        raise ApplianceError(f"{label} sequence state is invalid")
    return value["sequence"]


def _backup_public_identity(layout: Layout) -> tuple[str, bytes]:
    try:
        appliance_id = layout.appliance_id.read_text(encoding="ascii").strip()
        public_bytes = layout.backup_signing_public.read_bytes()
    except (OSError, UnicodeError) as exc:
        raise ApplianceError("Appliance backup signing identity is missing") from exc
    if (
        layout.appliance_id.is_symlink()
        or layout.backup_signing_public.is_symlink()
        or not re.fullmatch(r"[0-9a-f]{32}", appliance_id)
        or len(public_bytes) != 32
    ):
        raise ApplianceError("Appliance backup signing identity is invalid")
    return appliance_id, public_bytes


def ensure_backup_signing_identity(layout: Layout):
    """Create or load the non-restored Appliance backup signing identity."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    temporary = layout.backup_identity.with_name(f".{layout.backup_identity.name}.{os.getpid()}.tmp")
    if layout.backup_identity.is_symlink():
        raise ApplianceError("Appliance backup signing identity path is unsafe")
    if not layout.backup_identity.exists():
        if layout.backups.exists() and any(layout.backups.glob("*.enc")):
            raise ApplianceError("cannot create a new backup signing identity beside existing archives")
        ensure_directory(layout.opt, 0o755)
        if temporary.exists() or temporary.is_symlink():
            durable_remove_tree(temporary)
        key = Ed25519PrivateKey.generate()
        private_bytes = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        public_bytes = key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        try:
            temporary.mkdir(mode=0o700)
            fsync_directory(temporary.parent)
            write_bytes_0600(temporary / "appliance-id", secrets.token_hex(16).encode("ascii") + b"\n")
            write_bytes_0600(temporary / "backup-signing.ed25519", private_bytes)
            write_bytes_0600(temporary / "backup-signing.ed25519.pub", public_bytes)
            write_json_0600(temporary / "backup-sequence.json", {"sequence": 0})
            write_json_0600(temporary / "last-restore-sequence.json", {"sequence": 0})
            fsync_release_tree(temporary)
            os.replace(temporary, layout.backup_identity)
            fsync_directory(layout.opt)
        finally:
            if temporary.exists() or temporary.is_symlink():
                durable_remove_tree(temporary)
        return key
    identity_metadata = layout.backup_identity.lstat()
    if (
        not stat.S_ISDIR(identity_metadata.st_mode)
        or identity_metadata.st_uid != os.geteuid()
        or stat.S_IMODE(identity_metadata.st_mode) != 0o700
    ):
        raise ApplianceError("Appliance backup signing identity directory is unsafe")
    identity_paths = (
        layout.appliance_id,
        layout.backup_signing_private,
        layout.backup_signing_public,
        layout.backup_sequence,
        layout.last_restore_sequence,
    )
    if any(path.is_symlink() or not path.is_file() for path in identity_paths):
        raise ApplianceError("Appliance backup signing identity is incomplete")
    private_path = layout.backup_signing_private
    metadata = private_path.lstat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ApplianceError("Appliance backup signing private key permissions are unsafe")
    try:
        key = Ed25519PrivateKey.from_private_bytes(private_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise ApplianceError("Appliance backup signing private key is invalid") from exc
    _appliance_id, public_bytes = _backup_public_identity(layout)
    expected = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    if public_bytes != expected:
        raise ApplianceError("Appliance backup signing identity does not match")
    _read_sequence(layout.backup_sequence, "backup")
    _read_sequence(layout.last_restore_sequence, "restore")
    return key


def _next_backup_sequence(layout: Layout) -> int:
    sequence = _read_sequence(layout.backup_sequence, "backup") + 1
    write_json_0600(layout.backup_sequence, {"sequence": sequence})
    return sequence


def _trust_fingerprint(layout: Layout, path: Path, label: str) -> str:
    if path.is_file() and not path.is_symlink():
        return sha256(path)
    if layout.root == Path("/"):
        raise ApplianceError(f"production backup requires the installed {label} trust root")
    return "unprovisioned"


def backup_provenance_paths(archive: Path) -> tuple[Path, Path]:
    statement = Path(f"{archive}.provenance.json")
    return statement, Path(f"{statement}.sig")


def write_backup_provenance(layout: Layout, archive: Path, sequence: int, key: Any) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization

    appliance_id, public_bytes = _backup_public_identity(layout)
    recipient = _load_backup_public_key(layout.backup_recipient)
    recipient_bytes = recipient.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    statement = {
        "schema_version": 1,
        "appliance_id": appliance_id,
        "sequence": sequence,
        "archive_sha256": sha256(archive),
        "backup_signing_key_sha256": hashlib.sha256(public_bytes).hexdigest(),
        "recipient_key_sha256": hashlib.sha256(recipient_bytes).hexdigest(),
        "release_trust_sha256": _trust_fingerprint(layout, layout.release_key, "release-signing"),
        "attestation_trust_sha256": _trust_fingerprint(layout, layout.attestation_key, "CI-attestation"),
    }
    payload = canonical_manifest(statement)
    statement_path, signature_path = backup_provenance_paths(archive)
    write_bytes_0600(statement_path, payload)
    write_bytes_0600(signature_path, base64.b64encode(key.sign(payload)) + b"\n")
    return statement


def verify_backup_provenance(
    layout: Layout,
    archive: Path,
    *,
    enforce_replay: bool,
    allow_replay: bool,
) -> dict[str, Any]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    appliance_id, public_bytes = _backup_public_identity(layout)
    statement_path, signature_path = backup_provenance_paths(archive)
    try:
        statement = json.loads(statement_path.read_text(encoding="utf-8"))
        signature = base64.b64decode(signature_path.read_bytes().strip(), validate=True)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ApplianceError("backup provenance statement or signature is missing") from exc
    required = {
        "schema_version", "appliance_id", "sequence", "archive_sha256",
        "backup_signing_key_sha256", "recipient_key_sha256",
        "release_trust_sha256", "attestation_trust_sha256",
    }
    if not isinstance(statement, dict) or set(statement) != required or statement.get("schema_version") != 1:
        raise ApplianceError("backup provenance statement is invalid")
    payload = canonical_manifest(statement)
    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, payload)
    except Exception as exc:
        if exc.__class__.__name__ == "InvalidSignature":
            raise ApplianceError("backup provenance signature does not verify") from exc
        raise
    recipient = _load_backup_public_key(layout.backup_recipient)
    recipient_bytes = recipient.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    expected = {
        "appliance_id": appliance_id,
        "archive_sha256": sha256(archive),
        "backup_signing_key_sha256": hashlib.sha256(public_bytes).hexdigest(),
        "recipient_key_sha256": hashlib.sha256(recipient_bytes).hexdigest(),
        "release_trust_sha256": _trust_fingerprint(layout, layout.release_key, "release-signing"),
        "attestation_trust_sha256": _trust_fingerprint(layout, layout.attestation_key, "CI-attestation"),
    }
    if any(statement.get(field) != value for field, value in expected.items()):
        raise ApplianceError("backup provenance does not match this Appliance trust identity")
    sequence = statement.get("sequence")
    if not isinstance(sequence, int) or sequence <= 0:
        raise ApplianceError("backup provenance sequence is invalid")
    if enforce_replay and not allow_replay and sequence <= _read_sequence(
        layout.last_restore_sequence, "restore"
    ):
        raise ApplianceError("backup replay is blocked; use the explicit replay recovery override")
    return statement


def record_restored_backup_sequence(layout: Layout, sequence: Any) -> None:
    if not isinstance(sequence, int) or sequence <= 0:
        raise ApplianceError("restore transaction backup sequence is invalid")
    current = _read_sequence(layout.last_restore_sequence, "restore")
    write_json_0600(layout.last_restore_sequence, {"sequence": max(current, sequence)})


def install_release_trust_keys(
    layout: Layout,
    release_key: bytes,
    attestation_key: bytes,
) -> None:
    if release_key == attestation_key:
        raise ApplianceError("release-signing and CI-attestation keys must be distinct")
    temporary = layout.trust_roots.with_name(f".{layout.trust_roots.name}.{os.getpid()}.tmp")
    layout.etc.mkdir(parents=True, exist_ok=True, mode=0o700)
    if layout.trust_roots.exists() or layout.trust_roots.is_symlink():
        try:
            existing_release = read_stable_file_bytes(
                layout.release_key,
                maximum=64 * 1024,
                owner_uid=os.geteuid(),
                reject_writable_by_others=True,
                safe_ancestors=True,
            )
            existing_attestation = read_stable_file_bytes(
                layout.attestation_key,
                maximum=64 * 1024,
                owner_uid=os.geteuid(),
                reject_writable_by_others=True,
                safe_ancestors=True,
            )
        except ReleaseContractError as exc:
            raise ApplianceError("existing Appliance trust roots are unsafe") from exc
        if existing_release == release_key and existing_attestation == attestation_key:
            return
        raise ApplianceError("Appliance trust roots are already provisioned with different keys")
    if temporary.exists() or temporary.is_symlink():
        durable_remove_tree(temporary)
    try:
        temporary.mkdir(mode=0o700)
        write_bytes_0600(temporary / layout.release_key.name, release_key)
        write_bytes_0600(temporary / layout.attestation_key.name, attestation_key)
        fsync_release_tree(temporary)
        os.replace(temporary, layout.trust_roots)
        fsync_directory(layout.etc)
        if (
            ed25519_public_bytes(layout.release_key)
            == ed25519_public_bytes(layout.attestation_key)
        ):
            raise ApplianceError("persisted release and CI trust roots must be distinct")
    except (OSError, ReleaseContractError) as exc:
        if layout.trust_roots.exists() or layout.trust_roots.is_symlink():
            durable_remove_tree(layout.trust_roots)
        raise ApplianceError("failed to persist the verified Appliance trust roots") from exc
    finally:
        if temporary.exists() or temporary.is_symlink():
            durable_remove_tree(temporary)


def _encrypt_backup(plaintext: Path, destination: Path, recipient_key: Path) -> None:
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise ApplianceError("cryptography is required for encrypted backups") from exc
    public_key = _load_backup_public_key(recipient_key)
    data_key = os.urandom(32)
    nonce = os.urandom(12)
    encrypted_key = public_key.encrypt(
        data_key,
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    header = json.dumps(
        {
            "algorithm": "RSA-OAEP-SHA256+AES-256-GCM",
            "encrypted_key": base64.b64encode(encrypted_key).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "version": 1,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    if len(header) > 16_384:
        raise ApplianceError("encrypted backup header is unexpectedly large")
    encryptor = Cipher(algorithms.AES(data_key), modes.GCM(nonce)).encryptor()
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with plaintext.open("rb") as source, open(temporary, "xb") as output:
            os.chmod(temporary, 0o600)
            output.write(BACKUP_MAGIC)
            output.write(header + b"\n")
            for chunk in iter(lambda: source.read(BACKUP_CHUNK_BYTES), b""):
                output.write(encryptor.update(chunk))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
        fsync_directory(destination.parent)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _decrypt_backup(archive: Path, destination: Path, private_key: Path) -> None:
    try:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError as exc:
        raise ApplianceError("cryptography is required for encrypted backups") from exc
    try:
        with archive.open("rb") as source:
            if source.readline() != BACKUP_MAGIC:
                raise ApplianceError("backup is not an encrypted KSI-CCTV archive")
            header = json.loads(source.readline(16_385).decode("ascii"))
            if header.get("version") != 1 or header.get("algorithm") != "RSA-OAEP-SHA256+AES-256-GCM":
                raise ApplianceError("backup encryption format is unsupported")
            encrypted_key = base64.b64decode(header["encrypted_key"], validate=True)
            nonce = base64.b64decode(header["nonce"], validate=True)
            ciphertext_start = source.tell()
            source.seek(0, os.SEEK_END)
            ciphertext_size = source.tell() - ciphertext_start - 16
            if ciphertext_size < 0:
                raise ApplianceError("encrypted backup is truncated")
            source.seek(-16, os.SEEK_END)
            tag = source.read(16)
            data_key = _load_backup_private_key(private_key).decrypt(
                encrypted_key,
                padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
            )
            decryptor = Cipher(algorithms.AES(data_key), modes.GCM(nonce, tag)).decryptor()
            source.seek(ciphertext_start)
            remaining = ciphertext_size
            with open(destination, "xb") as output:
                os.chmod(destination, 0o600)
                while remaining:
                    chunk = source.read(min(BACKUP_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ApplianceError("encrypted backup is truncated")
                    output.write(decryptor.update(chunk))
                    remaining -= len(chunk)
                output.write(decryptor.finalize())
                output.flush()
                os.fsync(output.fileno())
    except (InvalidTag, KeyError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        destination.unlink(missing_ok=True)
        raise ApplianceError("backup cannot be decrypted or has been tampered with") from exc


def create_backup(layout: Layout) -> Path:
    if not layout.backup_recipient.is_file():
        raise ApplianceError("backup recipient key is not configured")
    _load_backup_public_key(layout.backup_recipient)
    signing_key = ensure_backup_signing_identity(layout)
    sequence = _next_backup_sequence(layout)
    layout.backups.mkdir(parents=True, exist_ok=True)
    os.chmod(layout.backups, 0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    plaintext = layout.backups / f".ksi-cctv-{stamp}.tar.gz.tmp"
    archive = layout.backups / f"ksi-cctv-{stamp}.tar.gz.enc"
    temporary = layout.backups / f"ksi-cctv-backup-{stamp}-{secrets.token_hex(8)}"
    register_sensitive_staging(layout, temporary, plaintext)
    try:
        temporary.mkdir(mode=0o700)
        fsync_directory(temporary.parent)
        snapshots = (
            (layout.etc, temporary / "etc" / "ksi-cctv", False),
            (layout.data, temporary / "var" / "lib" / "ksi-cctv", True),
            (layout.log, temporary / "var" / "log" / "ksi-cctv", False),
        )
        for source, destination, sqlite_aware in snapshots:
            _snapshot_tree_for_backup(source, destination, sqlite_aware=sqlite_aware)
        manifest_path = temporary / "backup-manifest.json"
        write_json_0600(manifest_path, _backup_manifest(temporary))
        with tarfile.open(plaintext, "w:gz") as tar:
            for _source, snapshot, _sqlite_aware in snapshots:
                if snapshot.exists():
                    tar.add(snapshot, arcname=snapshot.relative_to(temporary).as_posix(), recursive=True)
            tar.add(manifest_path, arcname="backup-manifest.json", recursive=False)
        os.chmod(plaintext, 0o600)
        validate_backup(plaintext)
        _encrypt_backup(plaintext, archive, layout.backup_recipient)
        write_backup_provenance(layout, archive, sequence, signing_key)
    except Exception:
        clear_durable(archive)
        for sidecar in backup_provenance_paths(archive):
            clear_durable(sidecar)
        raise
    finally:
        clear_durable(plaintext)
        durable_remove_tree(temporary)
        unregister_sensitive_staging(layout, temporary, plaintext)
    return archive


def _copy_backup_tree(
    source_fd: int,
    destination: Path,
    relative: Path,
    *,
    sqlite_aware: bool,
    databases: list[Path],
) -> None:
    destination.mkdir(mode=0o700)
    with os.scandir(source_fd) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            item_relative = relative / entry.name
            try:
                item_stat = os.stat(entry.name, dir_fd=source_fd, follow_symlinks=False)
            except OSError as exc:
                raise ApplianceError(f"cannot inspect backup source: {item_relative}") from exc
            if stat.S_ISLNK(item_stat.st_mode):
                raise ApplianceError(f"backup source must not contain symlinks: {item_relative}")
            target = destination / entry.name
            if stat.S_ISDIR(item_stat.st_mode):
                try:
                    child_fd = os.open(
                        entry.name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=source_fd,
                    )
                except OSError as exc:
                    raise ApplianceError(f"cannot safely open backup directory: {item_relative}") from exc
                try:
                    opened_stat = os.fstat(child_fd)
                    if (opened_stat.st_dev, opened_stat.st_ino) != (item_stat.st_dev, item_stat.st_ino):
                        raise ApplianceError(f"backup directory changed during snapshot: {item_relative}")
                    _copy_backup_tree(
                        child_fd,
                        target,
                        item_relative,
                        sqlite_aware=sqlite_aware,
                        databases=databases,
                    )
                    os.chmod(target, stat.S_IMODE(item_stat.st_mode) & 0o777)
                finally:
                    os.close(child_fd)
                continue
            if not stat.S_ISREG(item_stat.st_mode):
                raise ApplianceError(f"backup source contains a special file: {item_relative}")
            try:
                item_fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=source_fd)
            except OSError as exc:
                raise ApplianceError(f"cannot safely open backup file: {item_relative}") from exc
            try:
                opened_stat = os.fstat(item_fd)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or (opened_stat.st_dev, opened_stat.st_ino) != (item_stat.st_dev, item_stat.st_ino)
                ):
                    raise ApplianceError(f"backup file changed during snapshot: {item_relative}")
                header = os.read(item_fd, 16)
                os.lseek(item_fd, 0, os.SEEK_SET)
                if sqlite_aware and header == b"SQLite format 3\x00":
                    databases.append(item_relative)
                target_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    while chunk := os.read(item_fd, BACKUP_CHUNK_BYTES):
                        remaining = memoryview(chunk)
                        while remaining:
                            remaining = remaining[os.write(target_fd, remaining):]
                    os.fsync(target_fd)
                finally:
                    os.close(target_fd)
                os.chmod(target, stat.S_IMODE(item_stat.st_mode) & 0o777)
            finally:
                os.close(item_fd)


def _snapshot_tree_for_backup(source: Path, destination: Path, *, sqlite_aware: bool) -> None:
    """Snapshot only regular files/directories without following source links."""
    try:
        source_stat = source.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(source_stat.st_mode) or stat.S_ISLNK(source_stat.st_mode):
        raise ApplianceError(f"backup source must be a real directory: {source}")
    try:
        source_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ApplianceError(f"cannot safely open backup source: {source}") from exc
    databases: list[Path] = []
    try:
        opened_stat = os.fstat(source_fd)
        if (opened_stat.st_dev, opened_stat.st_ino) != (source_stat.st_dev, source_stat.st_ino):
            raise ApplianceError(f"backup source changed during snapshot: {source}")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _copy_backup_tree(
            source_fd,
            destination,
            Path(),
            sqlite_aware=sqlite_aware,
            databases=databases,
        )
    finally:
        os.close(source_fd)
    os.chmod(destination, stat.S_IMODE(source_stat.st_mode) & 0o777)
    for relative in databases:
        target = destination / relative
        snapshot = target.with_name(f".{target.name}.sqlite-snapshot")
        source_connection = sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)
        target_connection = sqlite3.connect(snapshot)
        try:
            source_connection.backup(target_connection)
        except sqlite3.Error as exc:
            raise ApplianceError(f"cannot snapshot runtime database: {relative}") from exc
        finally:
            target_connection.close()
            source_connection.close()
        os.chmod(snapshot, 0o600)
        with snapshot.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(snapshot, target)
        fsync_directory(target.parent)
        for suffix in ("-wal", "-shm"):
            sidecar = target.with_name(f"{target.name}{suffix}")
            if sidecar.exists():
                clear_durable(sidecar)
    fsync_release_tree(destination)


def prepare_layout(layout: Layout) -> None:
    if layout.root != Path("/"):
        for parent in (
            layout.root,
            layout.root / "opt",
            layout.root / "etc",
            layout.root / "var",
            layout.root / "var" / "lib",
            layout.root / "var" / "log",
            layout.root / "var" / "backups",
        ):
            ensure_directory(parent, 0o755)
    for path, mode in (
        (layout.opt, 0o755),
        (layout.etc, 0o700),
        (layout.data, 0o700),
        (layout.log, 0o750),
        (layout.backups, 0o700),
        (layout.releases, 0o755),
    ):
        ensure_directory(path, mode)
    for path, mode in (
        (layout.config, 0o700),
        (layout.cameras, 0o700),
        (layout.outbox, 0o750),
        (layout.captures, 0o750),
        (layout.events, 0o750),
        (layout.models, 0o750),
        (layout.etc / "caddy-data", 0o700),
        (layout.etc / "caddy-config", 0o700),
    ):
        ensure_directory(path, mode)


def _chown_runtime_tree(fd: int, relative: Path) -> None:
    try:
        os.fchown(fd, RUNTIME_UID, RUNTIME_GID)
    except PermissionError:
        raise
    with os.scandir(fd) as entries:
        for entry in sorted(entries, key=lambda item: item.name):
            item_relative = relative / entry.name
            try:
                item_stat = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
            except OSError as exc:
                raise ApplianceError(f"cannot inspect runtime ownership path: {item_relative}") from exc
            if stat.S_ISLNK(item_stat.st_mode):
                raise ApplianceError(f"runtime path must not be a symlink: {item_relative}")
            flags = os.O_RDONLY | os.O_NOFOLLOW
            if stat.S_ISDIR(item_stat.st_mode):
                flags |= os.O_DIRECTORY
            elif not stat.S_ISREG(item_stat.st_mode):
                raise ApplianceError(f"runtime path must be a regular file or directory: {item_relative}")
            try:
                item_fd = os.open(entry.name, flags, dir_fd=fd)
            except OSError as exc:
                raise ApplianceError(f"cannot safely open runtime ownership path: {item_relative}") from exc
            try:
                opened = os.fstat(item_fd)
                if (opened.st_dev, opened.st_ino) != (item_stat.st_dev, item_stat.st_ino):
                    raise ApplianceError(f"runtime path changed during ownership update: {item_relative}")
                if stat.S_ISDIR(opened.st_mode):
                    _chown_runtime_tree(item_fd, item_relative)
                else:
                    os.fchown(item_fd, RUNTIME_UID, RUNTIME_GID)
            finally:
                os.close(item_fd)


def prepare_runtime_ownership(layout: Layout) -> None:
    """Make engine-writable persistent paths match the image's non-root UID."""
    for root in (layout.data, layout.log):
        try:
            root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            raise ApplianceError(f"cannot safely open runtime ownership root: {root}") from exc
        try:
            _chown_runtime_tree(root_fd, Path(root.name))
        except PermissionError as exc:
            if layout.root == Path("/"):
                raise ApplianceError(
                    f"runtime path ownership requires root or delegated chown: {root}"
                ) from exc
        finally:
            os.close(root_fd)


def install_site_environment(layout: Layout, release: Path, source: str | None) -> None:
    """Install and validate the site env without ever printing its values."""
    compose = (release / "compose.yaml").read_text(encoding="utf-8")
    if not all(
        name in compose
        for name in ("/etc/ksi-cctv/engine.env", "/etc/ksi-cctv/web.env")
    ):
        return
    source_path = Path(source) if source else layout.site_environment
    try:
        content_bytes = read_stable_file_bytes(
            source_path,
            maximum=64 * 1024,
            owner_uid=os.geteuid(),
            owner_only=True,
            safe_ancestors=True,
        )
    except ReleaseContractError as exc:
        raise ApplianceError(
            "site environment is required; provide --site-env with API, web password, web secret, and capture key"
        ) from exc
    try:
        content = content_bytes.decode("utf-8")
    except UnicodeError as exc:
        raise ApplianceError("site environment is not readable UTF-8") from exc
    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    minimum_lengths = {
        "KSI_CCTV_API_KEY": 24,
        "KSI_CCTV_SITE_ADDRESS": 3,
        "KSI_CCTV_WEB_PASSWORD": 12,
        "KSI_CCTV_WEB_SECRET": 32,
        "KSI_CCTV_CAPTURE_KEY": 44,
    }
    missing = sorted(
        key
        for key in REQUIRED_SITE_ENV
        if not values.get(key)
        or len(values[key]) < minimum_lengths[key]
        or "change-me" in values[key].lower()
        or "replace" in values[key].lower()
    )
    if missing:
        raise ApplianceError(f"site environment is missing required secure values: {', '.join(missing)}")
    if not valid_fernet_key(values["KSI_CCTV_CAPTURE_KEY"]):
        raise ApplianceError("KSI_CCTV_CAPTURE_KEY must be a valid Fernet key")
    if len({values[key] for key in DISTINCT_SECRET_KEYS}) != len(DISTINCT_SECRET_KEYS):
        raise ApplianceError("site environment secrets must be distinct")
    forbidden = sorted(
        key
        for key in FORBIDDEN_PRODUCTION_ENV
        if values.get(key, "").strip().lower() in TRUTHY_ENV_VALUES
    )
    if forbidden:
        raise ApplianceError(
            f"site environment enables development-only options: {', '.join(forbidden)}"
        )
    unexpected = sorted(set(values) - REQUIRED_SITE_ENV)
    if unexpected:
        raise ApplianceError(f"site environment contains unsupported variables: {', '.join(unexpected)}")
    for key in ("KSI_CCTV_API_KEY", "KSI_CCTV_WEB_SECRET", "KSI_CCTV_CAPTURE_KEY"):
        if not valid_fernet_key(values[key]) or len(set(values[key])) < 8:
            raise ApplianceError(f"{key} must be a generated 32-byte URL-safe key")
    if (
        not re.fullmatch(r"[A-Za-z0-9._~!@%+=:,-]{16,128}", values["KSI_CCTV_WEB_PASSWORD"])
        or len(set(values["KSI_CCTV_WEB_PASSWORD"])) < 6
    ):
        raise ApplianceError(
            "KSI_CCTV_WEB_PASSWORD must use the generated Compose-safe password alphabet"
        )
    site_address = values["KSI_CCTV_SITE_ADDRESS"]
    labels = site_address.split(".")
    hostname_valid = (
        len(site_address) <= 253
        and re.fullmatch(r"[0-9.]+", site_address) is None
        and all(
            label
            and len(label) <= 63
            and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
            for label in labels
        )
    )
    try:
        address = ipaddress.ip_address(site_address)
        private_site_networks = (
            ipaddress.ip_network("10.0.0.0/8"),
            ipaddress.ip_network("172.16.0.0/12"),
            ipaddress.ip_network("192.168.0.0/16"),
        )
        ipv4_valid = (
            address.version == 4
            and (address.is_global or any(address in network for network in private_site_networks))
        )
    except ValueError:
        ipv4_valid = False
    if not hostname_valid and not ipv4_valid:
        raise ApplianceError("KSI_CCTV_SITE_ADDRESS must be a hostname or usable IPv4 address without a port")
    def env_bytes(keys: Sequence[str]) -> bytes:
        return "".join(f"{key}={values[key]}\n" for key in keys).encode("utf-8")

    write_bytes_0600(layout.site_environment, env_bytes(("KSI_CCTV_SITE_ADDRESS",)))
    write_bytes_0600(
        layout.engine_environment,
        env_bytes(("KSI_CCTV_API_KEY", "KSI_CCTV_CAPTURE_KEY")),
    )
    write_bytes_0600(
        layout.web_environment,
        env_bytes(("KSI_CCTV_API_KEY", "KSI_CCTV_WEB_PASSWORD", "KSI_CCTV_WEB_SECRET")),
    )


def initialize_defaults(layout: Layout, release: Path) -> None:
    defaults = release / "defaults" / "config"
    if not defaults.exists():
        return
    if defaults.is_symlink() or not defaults.is_dir():
        raise ApplianceError("release defaults/config must be a directory, not a symlink")
    for source in defaults.rglob("*"):
        relative = source.relative_to(defaults)
        target = layout.config / relative
        if source.is_symlink():
            raise ApplianceError(f"release defaults contains a symlink: {relative}")
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            os.chmod(target, 0o700)
        elif source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            os.chmod(target, 0o600)
        elif not source.is_file():
            raise ApplianceError(f"release defaults contains an unsupported entry: {relative}")


def stage_release(
    layout: Layout,
    source: Path,
    profile: str | None,
    public_key: Path | None,
    allow_unsigned: bool,
    attestation_public_key: Path | None = None,
    before_copy: Callable[[dict[str, Any], Path], None] | None = None,
    *,
    release_key_object: Any | None = None,
    attestation_key_object: Any | None = None,
) -> tuple[dict[str, Any], Path]:
    manifest = verify_release(
        source,
        profile,
        public_key,
        allow_unsigned,
        attestation_public_key,
        release_key_object=release_key_object,
        attestation_key_object=attestation_key_object,
    )
    destination = layout.releases / manifest["version"]
    if destination.exists():
        raise ApplianceError(f"release already staged: {destination}")
    if before_copy is not None:
        before_copy(manifest, destination)
    layout.releases.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, destination, symlinks=False)
        normalize_staged_release(destination, require_root_owner=layout.root == Path("/"))
        fsync_release_tree(destination)
        fsync_directory(destination.parent)
        verify_release(
            destination,
            profile,
            public_key,
            allow_unsigned,
            attestation_public_key,
            release_key_object=release_key_object,
            attestation_key_object=attestation_key_object,
        )
        verify_immutable_release(destination, manifest, require_root_owner=layout.root == Path("/"))
    except Exception as exc:
        remove_staged_release(destination)
        if isinstance(exc, ApplianceError):
            raise
        raise ApplianceError(f"failed to stage immutable release: {type(exc).__name__}") from exc
    return manifest, destination


def normalize_staged_release(release: Path, *, require_root_owner: bool) -> None:
    if require_root_owner and os.geteuid() != 0:
        raise ApplianceError("production release staging requires root ownership")
    paths = [release, *sorted(release.rglob("*"))]
    for path in paths:
        if path.is_symlink():
            raise ApplianceError(f"staged release contains a symlink: {path.relative_to(release)}")
        if require_root_owner:
            os.chown(path, 0, 0)
        os.chmod(path, 0o555 if path.is_dir() else 0o444)


def fsync_release_tree(release: Path) -> None:
    directories: list[Path] = [release]
    for path in sorted(release.rglob("*")):
        if path.is_file():
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        elif path.is_dir():
            directories.append(path)
    for directory in reversed(directories):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def verify_immutable_release(
    release: Path,
    manifest: dict[str, Any],
    *,
    require_root_owner: bool,
) -> None:
    expected_owner = 0 if require_root_owner else os.geteuid()
    for path in [release, *sorted(release.rglob("*"))]:
        relative = path.relative_to(release).as_posix() if path != release else "."
        if path.is_symlink() or path.stat().st_uid != expected_owner:
            raise ApplianceError(f"staged release ownership is unsafe: {relative}")
        expected_mode = 0o555 if path.is_dir() else 0o444
        if stat.S_IMODE(path.stat().st_mode) != expected_mode:
            raise ApplianceError(f"staged release permissions are unsafe: {relative}")
    for name, expected in manifest["files"].items():
        candidate = release / name
        if not candidate.is_file() or sha256(candidate).lower() != expected.lower():
            raise ApplianceError(f"staged release checksum verification failed: {name}")


def remove_staged_release(release: Path) -> None:
    if not release.exists():
        return
    for path in [release, *sorted(release.rglob("*"))]:
        if path.is_dir() and not path.is_symlink():
            with contextlib.suppress(OSError):
                os.chmod(path, 0o700)
    shutil.rmtree(release, ignore_errors=True)
    if release.parent.is_dir():
        fsync_directory(release.parent)


def validate_backup(archive: Path) -> None:
    roots = ("etc/ksi-cctv", "var/lib/ksi-cctv", "var/log/ksi-cctv")
    try:
        with tarfile.open(archive, "r:*") as tar:
            total_size = 0
            names: set[str] = set()
            entries: dict[str, dict[str, Any]] = {}
            manifest_bytes: bytes | None = None
            for count, member in enumerate(tar, start=1):
                if count > MAX_BACKUP_MEMBERS:
                    raise ApplianceError(f"backup exceeds {MAX_BACKUP_MEMBERS} member limit")
                total_size += member.size
                if total_size > MAX_BACKUP_UNCOMPRESSED_BYTES:
                    raise ApplianceError("backup exceeds uncompressed size limit")
                name = member.name
                posix = PurePosixPath(name)
                if (
                    not name
                    or posix.is_absolute()
                    or posix.as_posix() != name
                    or "\\" in name
                    or ".." in posix.parts
                    or name in names
                ):
                    raise ApplianceError(f"backup contains a duplicate or noncanonical path: {member.name}")
                names.add(name)
                if name != "backup-manifest.json" and not any(
                    name == root or name.startswith(root + "/") for root in roots
                ):
                    raise ApplianceError(f"backup contains unexpected path: {member.name}")
                if not member.isfile() and not member.isdir():
                    raise ApplianceError(f"backup contains unsafe member: {member.name}")
                mode = member.mode & 0o777
                if member.isdir():
                    entries[name] = {"mode": mode, "type": "directory"}
                    continue
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise ApplianceError(f"backup file cannot be read: {member.name}")
                digest = hashlib.sha256()
                content = bytearray() if name == "backup-manifest.json" else None
                while chunk := extracted.read(BACKUP_CHUNK_BYTES):
                    digest.update(chunk)
                    if content is not None:
                        if len(content) + len(chunk) > 4 * 1024 * 1024:
                            raise ApplianceError("backup manifest exceeds size limit")
                        content.extend(chunk)
                if content is not None:
                    if mode != 0o600:
                        raise ApplianceError("backup manifest permissions are invalid")
                    manifest_bytes = bytes(content)
                else:
                    entries[name] = {
                        "mode": mode,
                        "sha256": digest.hexdigest(),
                        "size": member.size,
                        "type": "file",
                    }
            if manifest_bytes is None:
                raise ApplianceError("backup manifest is missing")
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ApplianceError("backup manifest is invalid") from exc
            if (
                not isinstance(manifest, dict)
                or set(manifest) != {"schema_version", "roots", "entries"}
                or manifest.get("schema_version") != 1
                or manifest.get("roots") != list(roots)
                or manifest.get("entries") != entries
                or any(entries.get(root, {}).get("type") != "directory" for root in roots)
            ):
                raise ApplianceError("backup manifest does not match the complete archive payload")
    except (OSError, tarfile.TarError) as exc:
        raise ApplianceError(f"invalid backup archive: {archive}") from exc


def service_action(action: str) -> int:
    result = run_command(["systemctl", action, SERVICE])
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def authorized_service_action(layout: Layout, action: str) -> int:
    """Authorize one service start/restart while durable recovery state exists."""
    if action not in {"start", "restart"}:
        return service_action(action)
    if layout.root == Path("/") and os.geteuid() != 0:
        raise ApplianceError("transaction-bound service start authorization requires root")
    try:
        process_stat = (Path("/proc") / str(os.getpid()) / "stat").read_text(encoding="ascii")
        start_time = process_stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError) as exc:
        raise ApplianceError("cannot bind service start authorization to the operator process") from exc
    layout.start_authorization.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(layout.start_authorization.parent, 0o700)
    write_json_0600(
        layout.start_authorization,
        {"pid": os.getpid(), "start_time": start_time},
    )
    try:
        return service_action(action)
    finally:
        clear_durable(layout.start_authorization)


def install_systemd_unit(layout: Layout, release: Path, *, enable_service: bool = True) -> None:
    """Install the signed release unit before any service activation."""
    verify_immutable_release(
        release,
        load_manifest(release),
        require_root_owner=layout.root == Path("/"),
    )
    source = release / "systemd" / SERVICE
    if not source.is_file():
        raise ApplianceError("release is missing the signed systemd unit")
    destination = layout.root / "etc" / "systemd" / "system" / SERVICE
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        with source.open("rb") as source_handle, temporary.open("wb") as target_handle:
            shutil.copyfileobj(source_handle, target_handle)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
        directory_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)
    for command in (
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable" if enable_service else "disable", SERVICE],
    ):
        if run_command(command).returncode:
            raise ApplianceError(f"failed to install systemd unit: {' '.join(command)}")


def load_images(
    release: Path,
    manifest: dict[str, Any],
    *,
    layout: Layout | None = None,
) -> None:
    images: dict[str, str] = manifest["images"]
    for image, name in sorted(images.items()):
        try:
            validate_single_image_archive(release / name, image)
        except ReleaseContractError as exc:
            raise ApplianceError(str(exc)) from exc
    root_result = run_command(["docker", "info", "--format", "{{.DockerRootDir}}"])
    docker_root = Path(root_result.stdout.strip()) if not root_result.returncode else Path()
    if not docker_root.is_absolute() or not docker_root.is_dir():
        raise ApplianceError("Docker data-root cannot be measured before image loading")
    try:
        storage = os.statvfs(docker_root)
        available = storage.f_bavail * storage.f_frsize
    except OSError as exc:
        raise ApplianceError("Docker data-root free space cannot be measured") from exc
    archive_bytes = sum((release / name).stat().st_size for name in images.values())
    required = max(5 * 1024**3, archive_bytes * 2)
    if available < required:
        raise ApplianceError("Docker data-root does not have enough free space for the release images")
    preexisting = {
        image
        for image in images
        if run_command(["docker", "image", "inspect", image]).returncode == 0
    }
    loaded: list[str] = []
    pending: str | None = None
    cleanup_failed_images: list[str] = []
    transaction = load_transaction(layout) if layout is not None else None

    def record_progress() -> None:
        if layout is None or transaction is None:
            return
        transaction["image_load"] = {
            "introduced": [image for image in loaded if image not in preexisting],
            "pending": pending,
            "preexisting": sorted(preexisting),
            "cleanup_failed": cleanup_failed_images,
        }
        write_transaction(layout, transaction, str(transaction["phase"]))

    record_progress()
    failure: Exception | None = None
    try:
        for image, name in sorted(images.items()):
            pending = image
            record_progress()
            result = run_command(["docker", "image", "load", "--input", str(release / name)])
            if result.returncode:
                raise ApplianceError(f"failed to load offline image archive: {name}")
            loaded.append(image)
            pending = None
            record_progress()
            inspect = run_command([
                "docker", "image", "inspect", "--format", "{{.Id}} {{.Architecture}}", image,
            ])
            if inspect.returncode:
                raise ApplianceError("loaded image does not match a required Compose digest")
            if inspect.stdout.split() != [image, "arm64"]:
                raise ApplianceError(f"loaded image identity or architecture is invalid: {image}")
        verify_loaded_model_inventory(release, manifest)
    except Exception as exc:
        failure = exc
    if failure is not None:
        candidates = [*loaded]
        if pending is not None and pending not in candidates:
            candidates.append(pending)
        for image in reversed(candidates):
            if image in preexisting:
                continue
            present = run_command(["docker", "image", "inspect", image]).returncode == 0
            if present and run_command(["docker", "image", "rm", image]).returncode:
                cleanup_failed_images.append(image)
        pending = None
        record_progress()
        if cleanup_failed_images:
            raise ImageCleanupUnsafeError(
                "image loading failed and introduced-image cleanup is incomplete; inspect the transaction journal"
            ) from failure
        if isinstance(failure, ApplianceError):
            raise failure
        raise ApplianceError(f"image loading failed: {type(failure).__name__}") from failure


def cleanup_transaction_images(transaction: dict[str, Any]) -> None:
    state = transaction.get("image_load")
    if state is None:
        return
    if not isinstance(state, dict):
        raise ApplianceError("transaction image-load journal is invalid")
    preexisting = state.get("preexisting")
    introduced = state.get("introduced")
    pending = state.get("pending")
    cleanup_failed = state.get("cleanup_failed")
    if (
        not isinstance(preexisting, list)
        or not isinstance(introduced, list)
        or pending is not None and not isinstance(pending, str)
        or not isinstance(cleanup_failed, list)
        or any(not isinstance(image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image) for image in [*preexisting, *introduced, *cleanup_failed])
        or pending is not None and not re.fullmatch(r"sha256:[0-9a-f]{64}", pending)
    ):
        raise ApplianceError("transaction image-load journal is invalid")
    candidates = set(introduced) | set(cleanup_failed)
    if pending is not None:
        candidates.add(pending)
    candidates -= set(preexisting)
    for image in sorted(candidates):
        if (
            run_command(["docker", "image", "inspect", image]).returncode == 0
            and run_command(["docker", "image", "rm", image]).returncode
        ):
            raise ApplianceError(f"cannot remove interrupted candidate image: {image}")
    transaction.pop("image_load", None)


def preserve_image_load_journal(layout: Layout, transaction: dict[str, Any]) -> None:
    latest = load_transaction(layout)
    if latest is not None and "image_load" in latest:
        transaction["image_load"] = latest["image_load"]


def verify_loaded_model_inventory(release: Path, manifest: dict[str, Any]) -> None:
    try:
        inventory = json.loads((release / manifest["model_inventory"]).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ApplianceError("model inventory cannot be inspected after image load") from exc
    models = inventory.get("models") if isinstance(inventory, dict) else None
    if not isinstance(models, list) or not models:
        raise ApplianceError("model inventory has no models to verify")
    engine_image = compose_service_images(release)["engine"]
    create = run_command([
        "docker", "create", "--entrypoint", "/bin/true", engine_image,
    ])
    container_id = create.stdout.strip()
    if create.returncode or not re.fullmatch(r"[A-Za-z0-9_.-]+", container_id):
        raise ApplianceError("cannot create an inert engine container for model verification")
    verification_error: Exception | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="ksi-cctv-model-verify-") as temp:
            destination = Path(temp) / "models"
            destination.mkdir()
            copied = run_command(["docker", "cp", f"{container_id}:/app/models/.", str(destination)])
            if copied.returncode:
                raise ApplianceError("cannot extract engine model files for verification")
            expected: dict[str, str] = {}
            for model in models:
                if not isinstance(model, dict) or model.get("image") != engine_image:
                    raise ApplianceError("model inventory contains a non-engine model")
                path = model.get("path")
                digest = model.get("sha256")
                if not isinstance(path, str) or not path.startswith("/app/models/") or not isinstance(digest, str):
                    raise ApplianceError("model inventory path or digest is invalid")
                expected[path.removeprefix("/app/models/")] = digest
            actual: dict[str, str] = {}
            for path in destination.rglob("*"):
                if path.is_symlink():
                    raise ApplianceError("engine model directory contains a symlink")
                if path.is_file() and path.suffix.lower() in {".onnx", ".pt", ".engine"}:
                    actual[path.relative_to(destination).as_posix()] = sha256(path)
            if actual != expected:
                raise ApplianceError("engine model files do not match the signed model inventory")
    except Exception as exc:
        verification_error = exc
    finally:
        removed = run_command(["docker", "rm", "--force", container_id])
        if removed.returncode and verification_error is None:
            verification_error = ApplianceError("temporary model-verification container cleanup failed")
    if verification_error is not None:
        if isinstance(verification_error, ApplianceError):
            raise verification_error
        raise ApplianceError(
            f"engine model verification failed: {type(verification_error).__name__}"
        ) from verification_error


def run_manifest_health(manifest: dict[str, Any], health_timeout: int) -> None:
    health = manifest["healthcheck"]
    timeout = min(
        max(int(health.get("timeout_seconds", health_timeout)), 1),
        max(health_timeout, 1),
    )
    if run_command(healthcheck_command(manifest), timeout=timeout).returncode:
        raise ApplianceError("post-start health command failed")


def require_compatible_state(layout: Layout, manifest: dict[str, Any]) -> None:
    if current_release(layout) is None:
        return
    state_path = layout.etc / "appliance.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplianceError("installed appliance state metadata is missing or unreadable") from exc
    if state.get("state_schema") != manifest["state_schema"]:
        raise ApplianceError("release is incompatible with installed appliance state schema")
    installed_contract = state.get("persistent_state")
    if installed_contract is None:
        current = current_release(layout)
        installed_contract = load_manifest(current).get("persistent_state") if current else None
    if not persistent_contract_compatible(installed_contract, manifest.get("persistent_state")):
        raise ApplianceError("release persistent-state readers are not rollback compatible")


def activate_release(
    layout: Layout,
    target: Path,
    manifest: dict[str, Any],
    health_timeout: int,
    *,
    restore_unit_on_failure: bool = False,
    persistent_fingerprint: str | None = None,
    start_service: bool = True,
    restore_service_enabled: bool = True,
) -> None:
    verify_immutable_release(
        target,
        manifest,
        require_root_owner=layout.root == Path("/"),
    )
    previous = current_release(layout)
    try:
        load_images(target, manifest, layout=layout)
        atomic_switch(layout.current, target)
        if start_service:
            if authorized_service_action(layout, "restart"):
                raise ApplianceError("service restart failed after release switch")
            run_manifest_health(manifest, health_timeout)
    except ApplianceError as exc:
        if previous:
            if service_action("stop"):
                raise ApplianceError(
                    "release activation failed and candidate service could not be stopped"
                ) from exc
            atomic_switch(layout.current, previous)
            try:
                if restore_unit_on_failure:
                    install_systemd_unit(
                        layout, previous, enable_service=restore_service_enabled
                    )
            except ApplianceError as rollback_exc:
                raise ApplianceError(
                    f"release activation failed and rollback recovery failed: {type(rollback_exc).__name__}"
                ) from exc
            if persistent_fingerprint is not None:
                try:
                    state_changed = persistent_state_fingerprint(layout) != persistent_fingerprint
                except Exception as fingerprint_exc:
                    raise PersistentStateUnsafeError(
                        "candidate persistent data could not be verified; previous code was selected but the "
                        "service remains stopped pending operator integrity validation or encrypted-backup restore"
                    ) from fingerprint_exc
                if state_changed:
                    raise PersistentStateUnsafeError(
                        "candidate modified persistent data; previous code was selected but the service "
                        "remains stopped pending operator integrity validation or encrypted-backup restore"
                    ) from exc
            if start_service:
                try:
                    if authorized_service_action(layout, "restart"):
                        raise ApplianceError("previous service restart failed")
                    run_manifest_health(load_manifest(previous), health_timeout)
                except ApplianceError as rollback_exc:
                    raise ApplianceError(
                        f"release activation failed and rollback recovery failed: {type(rollback_exc).__name__}"
                    ) from exc
                raise ApplianceError(
                    f"release activation failed; previous release restored healthy: {exc}"
                ) from exc
            raise ApplianceError(
                f"release activation failed; previous release restored and remains stopped: {exc}"
            ) from exc
        else:
            stop_failed = service_action("stop") != 0
            if stop_failed:
                raise ApplianceError(
                    "release activation failed and the failed first-install stack could not be stopped"
                ) from exc
            layout.current.unlink(missing_ok=True)
            raise ApplianceError("release activation failed; failed first-install stack stopped") from exc


def cleanup_failed_first_install(layout: Layout, staged_release: Path) -> None:
    """Return a failed first install to a state where the signed bundle can be retried."""
    if service_action("disable"):
        raise ApplianceError(
            "failed first-install cleanup could not disable the service; "
            "the staged release and unit were retained for operator recovery"
        )
    installed_unit = layout.root / "etc" / "systemd" / "system" / SERVICE
    try:
        installed_unit.unlink(missing_ok=True)
        if run_command(["systemctl", "daemon-reload"]).returncode:
            raise ApplianceError("systemd daemon-reload failed")
        layout.current.unlink(missing_ok=True)
        remove_staged_release(staged_release)
    except (OSError, ApplianceError) as exc:
        raise ApplianceError(
            "failed first-install cleanup was incomplete; "
            "the staged release and remaining systemd state were retained for operator recovery"
        ) from exc


def verify_active_health(args: argparse.Namespace, layout: Layout) -> None:
    release = current_release(layout)
    if release is None:
        return
    manifest = verify_release(
        release,
        selected_hardware_profile(args.profile),
        trusted_key(args, layout),
        args.allow_unsigned,
        trusted_attestation_key(args, layout),
    )
    require_compatible_state(layout, manifest)
    run_manifest_health(manifest, args.health_timeout)


def command_status(args: argparse.Namespace, layout: Layout) -> int:
    release = current_release(layout)
    result = run_command(["systemctl", "is-active", SERVICE])
    print(json.dumps({"service": result.stdout.strip() or "unknown", "release": str(release) if release else None}))
    return 0 if result.returncode in (0, 3) else result.returncode


def service_is_active() -> bool:
    result = run_command(["systemctl", "is-active", SERVICE])
    if result.returncode == 0:
        return True
    if result.returncode == 3:
        return False
    raise ApplianceError("cannot determine whether the Appliance service is active")


def service_is_enabled() -> bool:
    result = run_command(["systemctl", "is-enabled", SERVICE])
    if result.returncode == 0:
        return True
    if result.returncode == 1 and result.stdout.strip() in {"disabled", "indirect"}:
        return False
    raise ApplianceError("cannot determine whether the Appliance service is enabled")


def command_doctor(args: argparse.Namespace, layout: Layout) -> int:
    profile = selected_hardware_profile(args.profile)
    failures = validate_hardware(profile) if profile else []
    warnings: list[str] = []
    checks: dict[str, str] = {}
    for name, command in {
        "docker": ["docker", "--version"],
        "compose": ["docker", "compose", "version"],
        "systemd": ["systemctl", "--version"],
    }.items():
        try:
            checks[name] = "available" if run_command(command, timeout=10).returncode == 0 else "unavailable"
        except (ApplianceError, FileNotFoundError):
            checks[name] = "unavailable"
        if checks[name] == "unavailable":
            failures.append(f"{name} is unavailable")
    if profile:
        try:
            runtimes = run_command(["docker", "info", "--format", "{{json .Runtimes}}"], timeout=15)
            if runtimes.returncode or "nvidia" not in runtimes.stdout.lower():
                warnings.append("NVIDIA Docker runtime/provider was not detected")
        except (ApplianceError, FileNotFoundError):
            warnings.append("unable to inspect NVIDIA Docker runtime/provider")
    try:
        available = os.statvfs(layout.data).f_bavail * os.statvfs(layout.data).f_frsize if layout.data.exists() else 0
        checks["storage_available_bytes"] = str(available)
        if available and available < 20 * 1024 * 1024 * 1024:
            warnings.append("less than 20 GiB is available for appliance data")
        if not available:
            warnings.append("appliance data directory does not exist; storage was not measured")
    except OSError:
        warnings.append("unable to measure appliance data storage")
    report: dict[str, Any] = {"layout": {}, "checks": checks, "failures": failures, "warnings": warnings}
    for key in ("opt", "etc", "data", "log", "backups"):
        report["layout"][key] = str(getattr(layout, key))
    release = current_release(layout)
    if release:
        try:
            verify_release(
                release,
                profile,
                trusted_key(args, layout),
                args.allow_unsigned,
                trusted_attestation_key(args, layout),
            )
            report["release"] = "verified"
        except ApplianceError as exc:
            report["release"] = str(exc)
    else:
        report["release"] = "not installed"
    print(json.dumps(report, sort_keys=True))
    return 1 if failures or report["release"] not in {"verified", "not installed"} else 0


def command_backup(args: argparse.Namespace, layout: Layout) -> int:
    require_confirmation(args)
    archive, _service_managed, _service_was_active = create_quiesced_backup(
        args, layout, keep_stopped=False
    )
    print(archive)
    return 0


def create_quiesced_backup(
    args: argparse.Namespace,
    layout: Layout,
    *,
    keep_stopped: bool,
    before_quiesce: Callable[[bool, bool], None] | None = None,
) -> tuple[Path, bool, bool]:
    """Create a backup while the managed runtime cannot mutate its source trees."""
    service_managed = current_release(layout) is not None
    service_was_active = service_managed and service_is_active()
    owns_transaction = before_quiesce is None and service_managed
    if before_quiesce is not None:
        before_quiesce(service_managed, service_was_active)
    elif owns_transaction:
        write_transaction(
            layout,
            {
                "operation": "backup",
                "service_managed": service_managed,
                "service_was_active": service_was_active,
            },
            "quiescing",
        )
    if service_managed and service_action("stop"):
        raise ApplianceError("service stop failed before backup")

    def restore_active_service() -> None:
        if authorized_service_action(layout, "restart"):
            raise ApplianceError("service restart failed")
        try:
            verify_active_health(args, layout)
        except Exception as health_exc:
            if service_action("stop"):
                raise ApplianceError("service health failed after backup and safe stop also failed") from health_exc
            raise ApplianceError("service health failed after backup; service was stopped") from health_exc

    try:
        archive = create_backup(layout)
    except Exception as backup_exc:
        if service_was_active:
            try:
                restore_active_service()
            except Exception as recovery_exc:
                raise ApplianceError(
                    f"backup failed and service recovery failed: {type(recovery_exc).__name__}: {recovery_exc}"
                ) from backup_exc
        if owns_transaction:
            clear_durable(layout.transaction)
        raise
    if service_was_active and not keep_stopped:
        try:
            restore_active_service()
        except Exception as restart_exc:
            raise ApplianceError(f"backup {archive} was created but active service recovery failed") from restart_exc
    if owns_transaction:
        clear_durable(layout.transaction)
    return archive, service_managed, service_was_active


def reconcile_restore_targets(layout: Layout, targets: Any, *, committed: bool) -> None:
    if not isinstance(targets, list):
        raise ApplianceError("restore transaction journal has no target plan")
    allowed_targets = {layout.etc.resolve(), layout.data.resolve(), layout.log.resolve()}
    for item in targets:
        if not isinstance(item, dict) or not isinstance(item.get("target"), str):
            raise ApplianceError("restore transaction target plan is invalid")
        target = Path(item["target"]).resolve()
        if target not in allowed_targets or not isinstance(item.get("had_old"), bool):
            raise ApplianceError("restore transaction target is outside the Appliance layout")
        old = target.with_name(f".{target.name}.pre-restore")
        candidate = target.with_name(f".{target.name}.restore-new")
        if committed:
            if old.exists():
                durable_remove_tree(old)
            if candidate.exists():
                durable_remove_tree(candidate)
        else:
            if old.exists():
                if target.exists():
                    durable_remove_tree(target)
                os.replace(old, target)
            elif item["had_old"] is False and target.exists() and not candidate.exists():
                durable_remove_tree(target)
            if candidate.exists():
                durable_remove_tree(candidate)
        fsync_directory(target.parent)


def _restore_plain_backup(
    args: argparse.Namespace,
    layout: Layout,
    archive: Path,
    provenance: dict[str, Any],
) -> int:
    require_confirmation(args)
    prepare_layout(layout)
    parent_transaction = load_transaction(layout)
    staging_parent = layout.root / "var" / "tmp"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = staging_parent / f"ksi-cctv-restore-{secrets.token_hex(12)}"
    register_sensitive_staging(layout, staging)
    try:
        staging.mkdir(mode=0o700)
        fsync_directory(staging.parent)
        with tarfile.open(archive, "r:*") as tar:
            tar.extractall(staging, filter="data")
        replacements = (
            (staging / "etc" / "ksi-cctv", layout.etc),
            (staging / "var" / "lib" / "ksi-cctv", layout.data),
            (staging / "var" / "log" / "ksi-cctv", layout.log),
        )
        targets = [
            {"target": str(target), "had_old": target.exists()}
            for source, target in replacements
            if source.exists()
        ]
        transaction: dict[str, Any] = {
            "operation": "restore",
            "archive_sha256": sha256(archive),
            "backup_sequence": provenance["sequence"],
            "targets": targets,
            "parent_transaction": parent_transaction,
            "staging": str(staging),
        }

        def journal_before_quiesce(service_managed: bool, service_was_active: bool) -> None:
            transaction["service_managed"] = service_managed
            transaction["service_was_active"] = service_was_active
            write_transaction(layout, transaction, "quiescing")

        safety_backup, service_managed, service_was_active = create_quiesced_backup(
            args,
            layout,
            keep_stopped=True,
            before_quiesce=journal_before_quiesce,
        )
        transaction["safety_backup"] = str(safety_backup)
        transaction["safety_backup_sha256"] = sha256(safety_backup)
        committed = False
        try:
            write_transaction(layout, transaction, "prepared")
            write_transaction(layout, transaction, "swapping")
            for source, target in replacements:
                if not source.exists():
                    continue
                candidate = target.with_name(f".{target.name}.restore-new")
                old = target.with_name(f".{target.name}.pre-restore")
                if candidate.exists():
                    durable_remove_tree(candidate)
                if old.exists():
                    durable_remove_tree(old)
                shutil.copytree(source, candidate, symlinks=False)
                fsync_release_tree(candidate)
                fsync_directory(candidate.parent)
                had_old = target.exists()
                if had_old:
                    os.replace(target, old)
                    fsync_directory(target.parent)
                os.replace(candidate, target)
                fsync_directory(target.parent)
            prepare_runtime_ownership(layout)
            if service_was_active:
                if authorized_service_action(layout, "restart"):
                    raise ApplianceError("service restart failed after restore")
                verify_active_health(args, layout)
            write_transaction(layout, transaction, "committed")
            committed = True
            reconcile_restore_targets(layout, targets, committed=True)
            durable_remove_tree(staging)
            record_restored_backup_sequence(layout, transaction["backup_sequence"])
            if parent_transaction is None:
                clear_durable(layout.transaction)
            else:
                write_json_0600(layout.transaction, parent_transaction)
        except Exception as restore_exc:
            if committed:
                raise ApplianceError(
                    "restore committed but durable cleanup is incomplete; run recover"
                ) from restore_exc
            recovery_error: Exception | None = None
            try:
                if service_was_active and service_action("stop"):
                    raise ApplianceError(
                        "restored service could not be stopped before filesystem recovery"
                    )
                reconcile_restore_targets(layout, targets, committed=False)
                if service_was_active:
                    if authorized_service_action(layout, "restart"):
                        raise ApplianceError("pre-restore service could not restart")
                    verify_active_health(args, layout)
                durable_remove_tree(staging)
                if parent_transaction is None:
                    clear_durable(layout.transaction)
                else:
                    write_json_0600(layout.transaction, parent_transaction)
            except Exception as exc:
                recovery_error = exc
            if recovery_error is not None:
                with contextlib.suppress(Exception):
                    write_transaction(layout, transaction, "recovery_failed")
                raise ApplianceError(
                    f"restore failed and recovery failed: {type(recovery_error).__name__}: {recovery_error}"
                ) from restore_exc
            raise
    finally:
        if staging.exists() or staging.is_symlink():
            durable_remove_tree(staging)
        unregister_sensitive_staging(layout, staging)
    print(f"restored {archive}; safety backup {safety_backup}")
    return 0


def _mount_filesystem_type(target: Path, mountinfo: str) -> str | None:
    selected: tuple[int, str] | None = None
    for line in mountinfo.splitlines():
        left, right = line.split(" - ", 1)
        fields = left.split()
        mountpoint_text = re.sub(
            r"\\([0-7]{3})",
            lambda match: chr(int(match.group(1), 8)),
            fields[4],
        )
        mountpoint = Path(mountpoint_text)
        try:
            target.relative_to(mountpoint)
        except ValueError:
            continue
        candidate = (len(mountpoint.parts), right.split()[0])
        if selected is None or candidate[0] > selected[0]:
            selected = candidate
    return selected[1] if selected is not None else None


def require_ephemeral_production_run(
    layout: Layout,
    target: Path | None = None,
    *,
    expected_mode: int | None = None,
) -> None:
    if layout.root != Path("/"):
        return
    run_root = layout.root / "run"
    try:
        metadata = run_root.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or stat.S_IMODE(metadata.st_mode) & 0o022
        ):
            raise ApplianceError("production /run ownership or permissions are unsafe")
        checked = target or run_root
        checked_metadata = checked.lstat()
        if (
            not stat.S_ISDIR(checked_metadata.st_mode)
            or stat.S_ISLNK(checked_metadata.st_mode)
            or checked_metadata.st_uid != 0
            or (expected_mode is not None and stat.S_IMODE(checked_metadata.st_mode) != expected_mode)
        ):
            raise ApplianceError("production validation staging ownership or permissions are unsafe")
        filesystem_type = _mount_filesystem_type(
            checked.resolve(strict=True),
            Path("/proc/self/mountinfo").read_text(encoding="utf-8"),
        )
    except (OSError, ValueError, IndexError) as exc:
        raise ApplianceError("cannot verify production /run as ephemeral storage") from exc
    if filesystem_type not in {"tmpfs", "ramfs"}:
        raise ApplianceError("restore validation requires production /run to be tmpfs or ramfs")


def _copy_backup_input(source: Path, destination: Path) -> None:
    try:
        metadata = source.lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise ApplianceError(f"backup input must be a regular non-symlink file: {source}")
        source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise ApplianceError(f"backup input is missing or unsafe: {source}") from exc
    try:
        opened = os.fstat(source_fd)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise ApplianceError("backup input changed while being opened")
        target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            while chunk := os.read(source_fd, BACKUP_CHUNK_BYTES):
                remaining = memoryview(chunk)
                while remaining:
                    remaining = remaining[os.write(target_fd, remaining):]
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)
    fsync_directory(destination.parent)


def stage_backup_inputs(source_archive: Path, staging: Path) -> Path:
    staged_archive = staging / "backup.tar.gz.enc"
    source_statement, source_signature = backup_provenance_paths(source_archive)
    staged_statement, staged_signature = backup_provenance_paths(staged_archive)
    for source, destination in (
        (source_archive, staged_archive),
        (source_statement, staged_statement),
        (source_signature, staged_signature),
    ):
        _copy_backup_input(source, destination)
    return staged_archive


def command_restore(args: argparse.Namespace, layout: Layout) -> int:
    if not args.backup_recipient_private_key:
        raise ApplianceError("restore requires --backup-recipient-private-key")
    archive = Path(args.archive).absolute()
    private_key = Path(args.backup_recipient_private_key).absolute()
    if not args.validate_only:
        require_confirmation(args)
    if args.validate_only:
        staging_parent = layout.root / "run" / "ksi-cctv-validation"
        ensure_directory(staging_parent, 0o700)
        require_ephemeral_production_run(layout, staging_parent, expected_mode=0o700)
        staging = staging_parent / f"backup-{secrets.token_hex(12)}"
        staging.mkdir(mode=0o700)
        fsync_directory(staging.parent)
        require_ephemeral_production_run(layout, staging, expected_mode=0o700)
        try:
            staged_archive = stage_backup_inputs(archive, staging)
            verify_backup_provenance(
                layout,
                staged_archive,
                enforce_replay=False,
                allow_replay=args.allow_backup_replay,
            )
            plaintext = staging / "backup.tar.gz"
            _decrypt_backup(staged_archive, plaintext, private_key)
            validate_backup(plaintext)
            print("backup validation passed")
            return 0
        finally:
            durable_remove_tree(staging)
    staging_parent = layout.root / "var" / "tmp"
    if staging_parent.is_symlink():
        raise ApplianceError("restore staging parent must not be a symlink")
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = staging_parent / f"ksi-cctv-backup-decrypt-{secrets.token_hex(12)}"
    register_sensitive_staging(layout, staging)
    try:
        staging.mkdir(mode=0o700)
        fsync_directory(staging.parent)
        staged_archive = stage_backup_inputs(archive, staging)
        provenance = verify_backup_provenance(
            layout,
            staged_archive,
            enforce_replay=True,
            allow_replay=args.allow_backup_replay,
        )
        plaintext = staging / "backup.tar.gz"
        _decrypt_backup(staged_archive, plaintext, private_key)
        validate_backup(plaintext)
        return _restore_plain_backup(args, layout, plaintext, provenance)
    finally:
        durable_remove_tree(staging)
        unregister_sensitive_staging(layout, staging)


def command_install(args: argparse.Namespace, layout: Layout) -> int:
    require_confirmation(args)
    profile = selected_hardware_profile(args.profile)
    if profile is None:
        raise ApplianceError("install requires --profile")
    issues = validate_hardware(profile)
    if issues:
        raise ApplianceError("; ".join(issues))
    if current_release(layout) is not None:
        raise ApplianceError("appliance is already installed; use upgrade")
    prepare_layout(layout)
    release_key_object = None
    attestation_key_object = None
    release_key_bytes = b""
    attestation_key_bytes = b""
    if not args.allow_unsigned:
        release_key_path = trusted_key(args, layout)
        attestation_key_path = trusted_attestation_key(args, layout)
        if release_key_path is None or attestation_key_path is None:
            raise ApplianceError("install requires external release and CI-attestation public keys")
        release_key_object, release_key_bytes = external_ed25519_key_material(
            Path(args.release_dir).resolve(), release_key_path, "release-signing"
        )
        attestation_key_object, attestation_key_bytes = external_ed25519_key_material(
            Path(args.release_dir).resolve(), attestation_key_path, "CI-attestation"
        )
        if release_key_bytes == attestation_key_bytes:
            raise ApplianceError("release-signing and CI-attestation keys must be distinct")
    if not args.backup_recipient_public_key:
        raise ApplianceError("install requires --backup-recipient-public-key for encrypted backups")
    _backup_key, backup_recipient_bytes = _backup_public_key_material(
        Path(args.backup_recipient_public_key)
    )
    transaction: dict[str, Any] = {}

    def journal_install(manifest: dict[str, Any], destination: Path) -> None:
        install_state = {
            "version": manifest["version"],
            "state_schema": manifest["state_schema"],
            "persistent_state": manifest["persistent_state"],
            "profile": profile,
            "installed_at": datetime.now(timezone.utc).isoformat(),
        }
        transaction.update({
            "operation": "install",
            "candidate": str(destination),
            "installed_state": install_state,
        })
        write_transaction(layout, transaction, "staging")

    manifest, destination = stage_release(
        layout,
        Path(args.release_dir).resolve(),
        profile,
        trusted_key(args, layout),
        args.allow_unsigned,
        trusted_attestation_key(args, layout),
        journal_install,
        release_key_object=release_key_object,
        attestation_key_object=attestation_key_object,
    )
    install_state = transaction["installed_state"]
    state_path = layout.etc / "appliance.json"
    unit_attempted = False
    try:
        write_transaction(layout, transaction, "staged")
        initialize_defaults(layout, destination)
        install_site_environment(layout, destination, args.site_env)
        install_backup_recipient(
            layout,
            args.backup_recipient_public_key,
            verified_bytes=backup_recipient_bytes,
        )
        if not args.allow_unsigned:
            install_release_trust_keys(layout, release_key_bytes, attestation_key_bytes)
        ensure_backup_signing_identity(layout)
        prepare_runtime_ownership(layout)
        unit_attempted = True
        write_transaction(layout, transaction, "unit_installing")
        install_systemd_unit(layout, destination)
        write_json_0600(state_path, install_state)
        write_transaction(layout, transaction, "activation_started")
        activate_release(layout, destination, manifest, args.health_timeout)
        clear_durable(layout.transaction)
    except Exception as install_exc:
        if caused_by(install_exc, ImageCleanupUnsafeError):
            with contextlib.suppress(Exception):
                preserve_image_load_journal(layout, transaction)
                write_transaction(layout, transaction, "recovery_failed")
            raise ApplianceError(
                "install image cleanup is incomplete; transaction journal retained for recover"
            ) from install_exc
        if "could not be stopped" in str(install_exc):
            with contextlib.suppress(Exception):
                write_transaction(layout, transaction, "recovery_failed")
            if isinstance(install_exc, ApplianceError):
                raise
            raise ApplianceError("first-install stack could not be stopped") from install_exc
        try:
            if unit_attempted:
                cleanup_failed_first_install(layout, destination)
            else:
                remove_staged_release(destination)
            clear_durable(state_path)
            clear_durable(layout.transaction)
        except ApplianceError as cleanup_exc:
            raise ApplianceError(f"install failed and cleanup failed: {cleanup_exc}") from install_exc
        if isinstance(install_exc, ApplianceError):
            raise
        raise ApplianceError(f"install failed before activation: {type(install_exc).__name__}") from install_exc
    print(f"installed {manifest['version']} for {profile}")
    return 0


def command_upgrade(args: argparse.Namespace, layout: Layout) -> int:
    require_confirmation(args)
    previous = current_release(layout)
    if previous is None:
        raise ApplianceError("appliance is not installed; use install")
    try:
        installed_state = json.loads((layout.etc / "appliance.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplianceError("installed appliance state metadata is missing or unreadable") from exc
    profile = selected_hardware_profile(args.profile or installed_state.get("profile"))
    if profile:
        issues = validate_hardware(profile)
        if issues:
            raise ApplianceError("; ".join(issues))
    source = Path(args.release_dir).resolve()
    prepare_layout(layout)
    service_was_active = service_is_active()
    service_was_enabled = service_is_enabled()
    prior_rollback = layout.rollback.read_bytes() if layout.rollback.is_file() else None
    transaction: dict[str, Any] = {
        "operation": "upgrade",
        "previous": str(previous),
        "installed_state": installed_state,
        "prior_rollback": base64.b64encode(prior_rollback).decode("ascii") if prior_rollback else None,
        "service_was_active": service_was_active,
        "service_was_enabled": service_was_enabled,
    }

    def journal_upgrade(_manifest: dict[str, Any], destination: Path) -> None:
        transaction["candidate"] = str(destination)
        write_transaction(layout, transaction, "staging")

    manifest, destination = stage_release(
        layout,
        source,
        profile,
        trusted_key(args, layout),
        args.allow_unsigned,
        trusted_attestation_key(args, layout),
        journal_upgrade,
    )
    try:
        require_compatible_state(layout, manifest)
        prepare_runtime_ownership(layout)
        write_transaction(layout, transaction, "staged")
    except Exception:
        remove_staged_release(destination)
        clear_durable(layout.transaction)
        raise
    if service_action("stop"):
        try:
            atomic_switch(layout.current, previous)
            install_systemd_unit(layout, previous, enable_service=service_was_enabled)
            if service_was_active:
                if authorized_service_action(layout, "restart"):
                    raise ApplianceError("previous service could not restart")
                run_manifest_health(load_manifest(previous), args.health_timeout)
            remove_staged_release(destination)
            clear_durable(layout.transaction)
        except Exception as recovery_exc:
            with contextlib.suppress(Exception):
                write_transaction(layout, transaction, "recovery_failed")
            raise ApplianceError(
                f"service stop failed and previous-service recovery failed: {recovery_exc}"
            ) from recovery_exc
        raise ApplianceError("service stop failed; previous release was restored healthy")
    state_path = layout.etc / "appliance.json"
    new_state = {
        **installed_state,
        "version": manifest["version"],
        "state_schema": manifest["state_schema"],
        "persistent_state": manifest["persistent_state"],
        "upgraded_at": datetime.now(timezone.utc).isoformat(),
    }
    backup: Path | None = None
    try:
        write_transaction(layout, transaction, "service_stopped")
        backup = create_backup(layout)
        transaction["backup"] = str(backup)
        transaction["backup_sha256"] = sha256(backup)
        write_transaction(layout, transaction, "backup_created")
        persistent_fingerprint = persistent_state_fingerprint(layout)
        rollback_metadata = {
            "previous": str(previous),
            "backup": str(backup),
            "backup_sha256": sha256(backup),
            "staged": str(destination),
            "previous_persistent_state": installed_state.get("persistent_state"),
            "candidate_persistent_state": manifest["persistent_state"],
        }
        write_json_0600(layout.rollback, rollback_metadata)
        write_transaction(layout, transaction, "metadata_written")
        write_json_0600(state_path, new_state)
        write_transaction(layout, transaction, "state_written")
        write_transaction(layout, transaction, "unit_installing")
        install_systemd_unit(layout, destination, enable_service=service_was_enabled)
        write_transaction(layout, transaction, "activation_started")
        activate_release(
            layout,
            destination,
            manifest,
            args.health_timeout,
            restore_unit_on_failure=True,
            persistent_fingerprint=persistent_fingerprint,
            start_service=service_was_active,
            restore_service_enabled=service_was_enabled,
        )
        clear_durable(layout.transaction)
    except Exception as upgrade_exc:
        recovery_error: Exception | None = None
        data_changed = isinstance(upgrade_exc, PersistentStateUnsafeError)
        image_cleanup_unsafe = caused_by(upgrade_exc, ImageCleanupUnsafeError)
        try:
            write_json_0600(state_path, installed_state)
            if prior_rollback is None:
                clear_durable(layout.rollback)
            else:
                write_bytes_0600(layout.rollback, prior_rollback)
            if "candidate service could not be stopped" in str(upgrade_exc):
                raise ApplianceError("candidate service stop failed; automatic recovery is unsafe")
            if not data_changed:
                atomic_switch(layout.current, previous)
                install_systemd_unit(layout, previous, enable_service=service_was_enabled)
                if service_was_active:
                    if authorized_service_action(layout, "restart"):
                        raise ApplianceError("previous service could not restart")
                    run_manifest_health(load_manifest(previous), args.health_timeout)
            if current_release(layout) != destination and not image_cleanup_unsafe:
                remove_staged_release(destination)
            if data_changed:
                write_transaction(layout, transaction, "data_changed")
            elif image_cleanup_unsafe:
                preserve_image_load_journal(layout, transaction)
                write_transaction(layout, transaction, "recovery_failed")
            else:
                clear_durable(layout.transaction)
        except Exception as exc:
            recovery_error = exc
        if recovery_error is not None:
            with contextlib.suppress(Exception):
                write_transaction(layout, transaction, "recovery_failed")
            raise ApplianceError(
                f"upgrade failed and recovery failed: {type(recovery_error).__name__}: {recovery_error}"
            ) from upgrade_exc
        if isinstance(upgrade_exc, ApplianceError):
            raise
        raise ApplianceError(f"upgrade failed before activation: {type(upgrade_exc).__name__}") from upgrade_exc
    assert backup is not None
    print(f"staged {manifest['version']}; backup {backup}")
    return 0


def command_rollback(args: argparse.Namespace, layout: Layout) -> int:
    require_confirmation(args)
    try:
        metadata = json.loads(layout.rollback.read_text(encoding="utf-8"))
        previous = metadata["previous"]
    except (OSError, ValueError, KeyError) as exc:
        raise ApplianceError("no valid rollback metadata") from exc
    if not previous:
        raise ApplianceError("there was no prior release to roll back to")
    target = Path(previous).resolve()
    active_before = current_release(layout)
    if active_before is None:
        raise ApplianceError("appliance has no active release to roll back from")
    service_was_active = service_is_active()
    service_was_enabled = service_is_enabled()
    try:
        target.relative_to(layout.releases.resolve())
    except ValueError as exc:
        raise ApplianceError("rollback target is outside releases directory") from exc
    manifest = verify_release(
        target,
        selected_hardware_profile(args.profile),
        trusted_key(args, layout),
        args.allow_unsigned,
        trusted_attestation_key(args, layout),
    )
    require_compatible_state(layout, manifest)
    if metadata.get("previous_persistent_state") != manifest.get("persistent_state"):
        raise ApplianceError("rollback target persistent-state contract does not match upgrade metadata")
    backup_path = Path(str(metadata.get("backup", "")))
    if not backup_path.is_file() or metadata.get("backup_sha256") != sha256(backup_path):
        raise ApplianceError("rollback backup metadata is missing or has been tampered with")
    prepare_runtime_ownership(layout)
    state_path = layout.etc / "appliance.json"
    installed_state = json.loads(state_path.read_text(encoding="utf-8"))
    rolled_back_state = {
        **installed_state,
        "version": manifest["version"],
        "state_schema": manifest["state_schema"],
        "persistent_state": manifest["persistent_state"],
        "rolled_back_at": datetime.now(timezone.utc).isoformat(),
    }
    transaction = {
        "operation": "rollback",
        "previous": str(active_before),
        "candidate": str(target),
        "installed_state": installed_state,
        "prior_rollback": base64.b64encode(layout.rollback.read_bytes()).decode("ascii"),
        "service_was_active": service_was_active,
        "service_was_enabled": service_was_enabled,
    }
    write_transaction(layout, transaction, "prepared")
    if service_action("stop"):
        try:
            atomic_switch(layout.current, active_before)
            install_systemd_unit(layout, active_before, enable_service=service_was_enabled)
            if service_was_active:
                if authorized_service_action(layout, "restart"):
                    raise ApplianceError("active service could not restart")
                run_manifest_health(load_manifest(active_before), args.health_timeout)
            clear_durable(layout.transaction)
        except Exception as recovery_exc:
            with contextlib.suppress(Exception):
                write_transaction(layout, transaction, "recovery_failed")
            raise ApplianceError(
                f"pre-rollback stop failed and active-service recovery failed: {recovery_exc}"
            ) from recovery_exc
        raise ApplianceError("pre-rollback stop failed; active release was restored healthy")
    rollback_backup: Path | None = None
    try:
        write_transaction(layout, transaction, "service_stopped")
        rollback_backup = create_backup(layout)
        transaction["backup"] = str(rollback_backup)
        transaction["backup_sha256"] = sha256(rollback_backup)
        write_transaction(layout, transaction, "backup_created")
        persistent_fingerprint = persistent_state_fingerprint(layout)
        write_json_0600(state_path, rolled_back_state)
        write_transaction(layout, transaction, "state_written")
        write_transaction(layout, transaction, "unit_installing")
        install_systemd_unit(layout, target, enable_service=service_was_enabled)
        write_transaction(layout, transaction, "activation_started")
        activate_release(
            layout,
            target,
            manifest,
            args.health_timeout,
            restore_unit_on_failure=True,
            persistent_fingerprint=persistent_fingerprint,
            start_service=service_was_active,
            restore_service_enabled=service_was_enabled,
        )
    except Exception as rollback_exc:
        recovery_error: Exception | None = None
        data_changed = isinstance(rollback_exc, PersistentStateUnsafeError)
        try:
            write_json_0600(state_path, installed_state)
            if "candidate service could not be stopped" in str(rollback_exc):
                raise ApplianceError("candidate service stop failed; automatic recovery is unsafe")
            if not data_changed:
                atomic_switch(layout.current, active_before)
                install_systemd_unit(layout, active_before, enable_service=service_was_enabled)
                if service_was_active:
                    if authorized_service_action(layout, "restart"):
                        raise ApplianceError("active service could not restart")
                    run_manifest_health(load_manifest(active_before), args.health_timeout)
            if data_changed:
                write_transaction(layout, transaction, "data_changed")
            else:
                clear_durable(layout.transaction)
        except Exception as exc:
            recovery_error = exc
        if recovery_error is not None:
            with contextlib.suppress(Exception):
                write_transaction(layout, transaction, "recovery_failed")
            raise ApplianceError(
                f"rollback failed and recovery failed: {type(recovery_error).__name__}: {recovery_error}"
            ) from rollback_exc
        if isinstance(rollback_exc, ApplianceError):
            raise
        raise ApplianceError(f"rollback failed before activation: {type(rollback_exc).__name__}") from rollback_exc
    clear_durable(layout.rollback)
    clear_durable(layout.transaction)
    assert rollback_backup is not None
    print(f"rolled back code to {target.name}; preserved current data in {rollback_backup}")
    return 0


def transaction_release_path(layout: Layout, value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ApplianceError(f"transaction journal has no valid {label} release")
    path = Path(value).resolve()
    try:
        path.relative_to(layout.releases.resolve())
    except ValueError as exc:
        raise ApplianceError(f"transaction {label} release is outside the release store") from exc
    return path


def transaction_restore_staging(layout: Layout, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ApplianceError("restore transaction journal has no staging path")
    path = Path(value).resolve()
    parent = (layout.root / "var" / "tmp").resolve()
    if path.parent != parent or not path.name.startswith("ksi-cctv-restore-"):
        raise ApplianceError("restore transaction staging path is outside the private restore area")
    return path


def command_recover(args: argparse.Namespace, layout: Layout) -> int:
    """Conservatively restore the pre-transaction code and metadata; never restore data automatically."""
    require_confirmation(args)
    cleaned_sensitive = cleanup_sensitive_staging(layout)
    transaction = load_transaction(layout)
    if transaction is None:
        if cleaned_sensitive:
            print("removed interrupted plaintext staging; no Appliance transaction remained")
            return 0
        raise ApplianceError("there is no interrupted Appliance transaction to recover")
    operation = transaction["operation"]
    phase = transaction.get("phase")
    if (
        operation != "restore"
        and phase in {"activation_started", "data_changed", "recovery_failed"}
        and not args.accept_live_data
    ):
        raise ApplianceError(
            "recovery may retain candidate-written data; inspect the encrypted backup and rerun with "
            "--accept-live-data or perform an explicit restore"
        )
    if operation == "backup":
        service_managed = transaction.get("service_managed") is True
        service_was_active = transaction.get("service_was_active")
        if not service_managed or not isinstance(service_was_active, bool):
            raise ApplianceError("backup transaction journal has an invalid prior service state")
        if service_action("stop"):
            raise ApplianceError("interrupted backup service could not be stopped")
        if service_was_active:
            if authorized_service_action(layout, "restart"):
                raise ApplianceError("pre-backup service could not restart")
            verify_active_health(args, layout)
        clear_durable(layout.transaction)
        print("recovered interrupted backup and restored the prior service state")
        return 0
    state_path = layout.etc / "appliance.json"
    if operation == "restore":
        targets = transaction.get("targets")
        service_managed = transaction.get("service_managed") is True
        service_was_active = transaction.get("service_was_active")
        if not isinstance(service_was_active, bool) or (service_was_active and not service_managed):
            raise ApplianceError("restore transaction journal has an invalid prior service state")
        staging = transaction_restore_staging(layout, transaction.get("staging"))
        if service_was_active and service_action("stop"):
            raise ApplianceError("restore transaction service could not be stopped")
        committed = phase == "committed"
        reconcile_restore_targets(layout, targets, committed=committed)
        if service_was_active:
            if authorized_service_action(layout, "restart"):
                raise ApplianceError("pre-restore service could not restart")
            verify_active_health(args, layout)
        durable_remove_tree(staging)
        if committed:
            record_restored_backup_sequence(layout, transaction.get("backup_sequence"))
        parent_transaction = transaction.get("parent_transaction")
        if parent_transaction is None:
            clear_durable(layout.transaction)
        elif isinstance(parent_transaction, dict):
            write_json_0600(layout.transaction, parent_transaction)
        else:
            raise ApplianceError("restore parent transaction is invalid")
        if committed:
            print("completed cleanup for the committed restore")
        else:
            print("recovered interrupted restore to its pre-restore filesystem state")
        return 0

    candidate = transaction_release_path(layout, transaction.get("candidate"), "candidate")
    if operation == "install":
        if phase in {"staging", "staged"}:
            remove_staged_release(candidate)
            clear_durable(state_path)
            clear_durable(layout.transaction)
            print("cleared interrupted pre-activation install")
            return 0
        if service_action("stop"):
            raise ApplianceError("interrupted first-install service could not be stopped")
        cleanup_transaction_images(transaction)
        write_transaction(layout, transaction, str(phase))
        unit = layout.root / "etc" / "systemd" / "system" / SERVICE
        if unit.is_file():
            cleanup_failed_first_install(layout, candidate)
        else:
            remove_staged_release(candidate)
        clear_durable(state_path)
        clear_durable(layout.transaction)
        print("cleared interrupted first install; no persistent backup was restored")
        return 0

    previous = transaction_release_path(layout, transaction.get("previous"), "previous")
    installed_state = transaction.get("installed_state")
    if not isinstance(installed_state, dict):
        raise ApplianceError("transaction journal is missing the previous Appliance state")
    service_was_active = transaction.get("service_was_active")
    service_was_enabled = transaction.get("service_was_enabled")
    if not isinstance(service_was_active, bool) or not isinstance(service_was_enabled, bool):
        raise ApplianceError("transaction journal is missing the previous service state")
    manifest = verify_release(
        previous,
        selected_hardware_profile(args.profile or installed_state.get("profile")),
        trusted_key(args, layout),
        args.allow_unsigned,
        trusted_attestation_key(args, layout),
    )
    verify_immutable_release(
        previous,
        manifest,
        require_root_owner=layout.root == Path("/"),
    )
    if service_was_active and service_action("stop"):
        raise ApplianceError("active transaction service could not be stopped during recovery")
    cleanup_transaction_images(transaction)
    write_transaction(layout, transaction, str(phase))
    install_systemd_unit(layout, previous, enable_service=service_was_enabled)
    atomic_switch(layout.current, previous)
    write_json_0600(state_path, installed_state)
    prior_rollback = transaction.get("prior_rollback")
    if prior_rollback is None:
        clear_durable(layout.rollback)
    elif isinstance(prior_rollback, str):
        try:
            write_bytes_0600(layout.rollback, base64.b64decode(prior_rollback, validate=True))
        except ValueError as exc:
            raise ApplianceError("transaction rollback metadata is invalid") from exc
    else:
        raise ApplianceError("transaction rollback metadata is invalid")
    if service_was_active:
        if authorized_service_action(layout, "restart"):
            raise ApplianceError("previous service could not restart during transaction recovery")
        run_manifest_health(manifest, args.health_timeout)
    if candidate != previous:
        remove_staged_release(candidate)
    clear_durable(layout.transaction)
    print(f"recovered {operation} to {previous.name}; persistent data was not restored")
    return 0


def trusted_key(args: argparse.Namespace, layout: Layout) -> Path | None:
    supplied = Path(args.public_key).resolve() if args.public_key else None
    if supplied is not None and layout.release_key.is_file():
        try:
            if ed25519_public_bytes(supplied) != ed25519_public_bytes(layout.release_key):
                raise ApplianceError("in-place release-signing key rotation is not supported in v1")
        except ReleaseContractError as exc:
            raise ApplianceError("release-signing trust key is invalid") from exc
    if supplied is not None:
        return supplied
    return layout.release_key if layout.release_key.is_file() else None


def trusted_attestation_key(args: argparse.Namespace, layout: Layout) -> Path | None:
    value = getattr(args, "attestation_public_key", None)
    if value:
        supplied = Path(value).resolve()
        if layout.attestation_key.is_file():
            try:
                if ed25519_public_bytes(supplied) != ed25519_public_bytes(layout.attestation_key):
                    raise ApplianceError("in-place CI-attestation key rotation is not supported in v1")
            except ReleaseContractError as exc:
                raise ApplianceError("CI-attestation trust key is invalid") from exc
        return supplied
    return layout.attestation_key if layout.attestation_key.is_file() else None


def command_lab(args: argparse.Namespace, layout: Layout) -> int:
    candidate = layout.opt / "lab" / "bin" / "ksi-cctv-lab"
    if not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ApplianceError("lab delegation is not installed")
    require_confirmation(args)
    result = run_command([str(candidate), *args.lab_args])
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def command_verify(args: argparse.Namespace, layout: Layout) -> int:
    """Verify a candidate bundle without touching installed appliance state."""
    manifest = verify_release(
        Path(args.release_dir),
        selected_hardware_profile(args.profile),
        trusted_key(args, layout),
        args.allow_unsigned,
        trusted_attestation_key(args, layout),
    )
    print(
        json.dumps(
            {
                "verified": True,
                "version": manifest["version"],
                "hardware_profiles": manifest["hardware_profiles"],
                "image_count": len(manifest["images"]),
            },
            sort_keys=True,
        )
    )
    return 0


def parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", default="/", help="filesystem root (for appliance tests or chroots)")
    common.add_argument("--profile", choices=sorted(PROFILES))
    common.add_argument("--public-key", help="trusted Ed25519 public key outside the release")
    common.add_argument(
        "--attestation-public-key",
        help="trusted CI Ed25519 attestation public key outside the release",
    )
    common.add_argument("--allow-unsigned", action="store_true", help="UNSAFE: permit an unsigned development release")
    common.add_argument("--health-timeout", type=int, default=DEFAULT_HEALTH_TIMEOUT, help="maximum health-check seconds")
    parser = argparse.ArgumentParser(description="KSI-CCTV offline appliance operator")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "doctor"):
        sub.add_parser(name, parents=[common])
    for name in ("start", "stop", "restart"):
        item = sub.add_parser(name, parents=[common])
        item.add_argument("--confirm", action="store_true")
    item = sub.add_parser("backup", parents=[common])
    item.add_argument("--confirm", action="store_true")
    item = sub.add_parser("install", parents=[common])
    item.add_argument("--release-dir", required=True)
    item.add_argument("--site-env", help="owner-only site environment file copied into /etc/ksi-cctv")
    item.add_argument("--backup-recipient-public-key", help="external PEM RSA public key required to encrypt backups")
    item.add_argument("--confirm", action="store_true")
    item = sub.add_parser("verify", parents=[common])
    item.add_argument("--release-dir", required=True)
    item = sub.add_parser("restore", parents=[common])
    item.add_argument("--archive", required=True)
    item.add_argument("--backup-recipient-private-key", help="external PEM RSA private key used only to decrypt this backup")
    item.add_argument("--confirm", action="store_true")
    item.add_argument("--validate-only", action="store_true")
    item.add_argument(
        "--allow-backup-replay",
        action="store_true",
        help="explicitly permit restoring a previously restored sequence from this Appliance",
    )
    item = sub.add_parser("upgrade", parents=[common])
    item.add_argument("--release-dir", required=True)
    item.add_argument("--confirm", action="store_true")
    item = sub.add_parser("rollback", parents=[common])
    item.add_argument("--confirm", action="store_true")
    item = sub.add_parser("recover", parents=[common])
    item.add_argument("--confirm", action="store_true")
    item.add_argument(
        "--accept-live-data",
        action="store_true",
        help="acknowledge that candidate-written persistent data will not be restored automatically",
    )
    item = sub.add_parser("lab", parents=[common])
    item.add_argument("--confirm", action="store_true")
    item.add_argument("lab_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    layout = Layout(Path(args.root))
    try:
        if args.allow_unsigned and layout.root == Path("/"):
            raise ApplianceError("--allow-unsigned is forbidden on the production root")
        if (
            args.allow_unsigned
            and args.command != "verify"
            and run_command is HOST_RUN_COMMAND
        ):
            raise ApplianceError(
                "--allow-unsigned mutating commands require an injected non-host test backend"
            )
        if args.command in {"start", "stop", "restart"}:
            require_confirmation(args)
        commands = {"status": command_status, "doctor": command_doctor, "install": command_install, "verify": command_verify, "backup": command_backup,
                    "restore": command_restore, "upgrade": command_upgrade, "rollback": command_rollback,
                    "recover": command_recover,
                    "lab": command_lab}

        def execute() -> int:
            if load_sensitive_staging(layout) and args.command not in {"status", "doctor", "recover"}:
                raise ApplianceError(
                    "interrupted plaintext staging requires the recover command before Appliance mutation"
                )
            transaction = load_transaction(layout)
            if transaction is not None:
                allowed = {"status", "doctor", "recover"}
                if transaction["operation"] != "restore":
                    allowed.add("restore")
                if args.command not in allowed:
                    raise ApplianceError(
                        f"interrupted {transaction['operation']} transaction requires the recover command"
                    )
            if args.command in {"start", "stop", "restart"}:
                return service_action(args.command)
            return commands[args.command](args, layout)

        if args.command in {"status", "doctor", "verify"}:
            return execute()
        with appliance_operation_lock(layout):
            return execute()
    except ApplianceError as exc:
        print(f"error: {mask_secrets(str(exc))}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
