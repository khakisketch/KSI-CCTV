"""Static supply-chain and persistent-state contracts for signed Appliance releases."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any, Protocol, cast
from urllib.parse import unquote_plus, urlsplit

from license_expression import ExpressionError, get_spdx_licensing

HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
PLACEHOLDER = re.compile(r"(?:REPLACE_|change-me|placeholder|example\.com)", re.IGNORECASE)
RUNNER = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
SPDX_REFERENCE = re.compile(r"^SPDXRef-[A-Za-z0-9.-]+$")
SPDX_LICENSING = get_spdx_licensing()
CONTRACT_PATH = "release-contract.json"
BUNDLE_ATTESTATION_PATH = "evidence/bundle-attestation.json"
BUNDLE_SIGNATURE_PATH = "evidence/bundle-attestation.json.sig"
EVIDENCE_KINDS = {"pytest", "skip", "pip", "npm", "playwright"}
STRICT_GATE_COMMAND = "bash scripts/verify_release_candidate.sh --strict"
EVIDENCE_SUMMARY_PATHS = {
    "pytest": "pytest-summary.json",
    "skip": "skip-summary.json",
    "pip": "pip-audit-summary.json",
    "npm": "npm-audit-summary.json",
    "playwright": "playwright-summary.json",
}
APPROVED_SKIPS = (
    (
        "tests.test_daemon_smoke.TestDaemonSmoke::test_daemon_boots_serves_health_and_stops_clean",
        "KSI_CCTV_TEST_DAEMON=1 + 실 ONNX/sample.mp4 필요(데몬 종단 smoke)",
    ),
    (
        "tests.test_e2e_real.TestRealVlmJourney::test_real_vlm_returns_structured_analysis",
        "실 VLM 테스트는 KSI_CCTV_TEST_VLM=1 + 실 자산 필요 (느림)",
    ),
    (
        "tests.test_openvocab.TestOpenVocabRealInference::test_text_prompt_detects_on_real_frame",
        "KSI_CCTV_TEST_OPENVOCAB=1 + ultralytics 필요(실 YOLO-World 추론, 느림·가중치 다운로드)",
    ),
    (
        "tests.test_virtual_cctv_lab::test_docker_compose_config_is_valid",
        "Docker integration test is opt-in: KSI_CCTV_LAB_INTEGRATION=1",
    ),
    (
        "tests.test_virtual_cctv_lab::test_managed_camera_api_first_frame_gateway_and_delete",
        "Docker integration test is opt-in: KSI_CCTV_LAB_INTEGRATION=1",
    ),
)
TOOL_VERSION_KEYS = {"python", "git", "node", "npm", "docker", "docker_compose"}
STORE_SCHEMAS = {
    "events": 2,
    "observations": 1,
    "incidents": 1,
    "cameras": 1,
    "commissioning": 1,
    "outbox": 1,
    "config": 1,
}
MAX_TEXT_PAYLOAD_BYTES = 64 * 1024 * 1024
MAX_IMAGE_LAYER_MEMBERS = 1_000_000
MAX_IMAGE_EXPANDED_BYTES = 64 * 1024**3
DEFAULT_CONFIG_PATHS = {
    "defaults/config/alert_preferences.yaml",
    "defaults/config/camera_profiles.yaml",
    "defaults/config/cameras.json",
    "defaults/config/default.yaml",
    "defaults/config/event_policies.yaml",
    "defaults/config/mediamtx.yml",
    "defaults/config/model_profiles.yaml",
    "defaults/config/prompts/__init__.py",
    "defaults/config/prompts/construction.yaml",
    "defaults/config/prompts/factory.yaml",
    "defaults/config/prompts/general.yaml",
    "defaults/config/prompts/parking.yaml",
    "defaults/config/prompts/school.yaml",
    "defaults/config/prompts/warehouse.yaml",
    "defaults/config/proximity.example.yaml",
    "defaults/config/webhooks.example.yaml",
    "defaults/config/zones.example.yaml",
}
RUNTIME_PAYLOAD_PATHS = {
    "compose.yaml",
    "Caddyfile",
    "mediamtx.yml",
    "bin/activation_health.py",
    "bin/transaction_guard.py",
    "systemd/ksi-cctv-compose.service",
}


class ReleaseContractError(ValueError):
    """Raised when signed release evidence is incomplete or inconsistent."""


class BinaryReader(Protocol):
    def read(self, size: int = -1) -> bytes: ...


class PrefixedReader:
    def __init__(self, prefix: bytes, source: BinaryReader):
        self._prefix = prefix
        self._source = source

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            result = self._prefix + self._source.read()
            self._prefix = b""
            return result
        if not self._prefix:
            return self._source.read(size)
        result = self._prefix[:size]
        self._prefix = self._prefix[size:]
        if len(result) < size:
            result += self._source.read(size - len(result))
        return result


def read_stable_file_bytes(
    path: Path,
    *,
    maximum: int,
    owner_uid: int | None = None,
    owner_only: bool = False,
    reject_writable_by_others: bool = False,
    safe_ancestors: bool = False,
) -> bytes:
    if safe_ancestors:
        absolute = Path(os.path.abspath(path))
        current = absolute.parent
        while True:
            try:
                metadata = current.lstat()
            except OSError as exc:
                raise ReleaseContractError(
                    f"cannot inspect external trust path safely: {path.name}"
                ) from exc
            mode = stat.S_IMODE(metadata.st_mode)
            sticky_root_boundary = (
                metadata.st_uid == 0
                and bool(mode & stat.S_ISVTX)
                and bool(mode & 0o002)
            )
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or (owner_uid is not None and metadata.st_uid not in {0, owner_uid})
                or (mode & 0o022 and not sticky_root_boundary)
            ):
                raise ReleaseContractError(
                    f"external trust path has unsafe ancestor custody: {path.name}"
                )
            if current == current.parent:
                break
            current = current.parent
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseContractError(f"cannot open external trust file safely: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > maximum
            or (owner_uid is not None and before.st_uid != owner_uid)
            or (owner_only and stat.S_IMODE(before.st_mode) & 0o077)
            or (reject_writable_by_others and stat.S_IMODE(before.st_mode) & 0o022)
        ):
            raise ReleaseContractError(f"external trust file has unsafe custody: {path.name}")
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
            raise ReleaseContractError(f"external trust file changed while being read: {path.name}")
        return content
    finally:
        os.close(descriptor)


def _stable_sha256(path: Path) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ReleaseContractError(f"release payload cannot be opened safely: {path.name}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ReleaseContractError(f"release payload is not a regular file: {path.name}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = os.fstat(descriptor)
        def identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
            return (
                value.st_dev,
                value.st_ino,
                value.st_size,
                value.st_mtime_ns,
                value.st_ctime_ns,
            )
        if identity(before) != identity(after):
            raise ReleaseContractError(f"release payload changed while being read: {path.name}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def _copy_stable_file(source: Path, destination: Path) -> str:
    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
        ):
            raise ReleaseContractError(f"release payload is not a single-link regular file: {source.name}")
        digest = hashlib.sha256()
        with os.fdopen(descriptor, "rb", closefd=False) as source_handle, destination.open("xb") as target_handle:
            for chunk in iter(lambda: source_handle.read(1024 * 1024), b""):
                digest.update(chunk)
                target_handle.write(chunk)
            target_handle.flush()
            os.fsync(target_handle.fileno())
        after = os.fstat(descriptor)
        if (
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
            raise ReleaseContractError(f"release payload changed during snapshot: {source.name}")
        os.chmod(destination, 0o400)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


@contextmanager
def release_payload_snapshot(release: Path) -> Iterator[Path]:
    """Copy a stable release tree into private staging for inspection and hashing."""
    try:
        with tempfile.TemporaryDirectory(prefix="ksi-cctv-release-snapshot-") as temporary:
            snapshot = Path(temporary) / "release"
            snapshot.mkdir(mode=0o700)
            release_metadata = release.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(release_metadata.st_mode)
                or release_metadata.st_uid != os.geteuid()
                or stat.S_IMODE(release_metadata.st_mode) & 0o022
            ):
                raise ReleaseContractError("release signing tree must be operator-owned and non-writable")
            for source in sorted(release.rglob("*")):
                relative = source.relative_to(release)
                if source.is_symlink():
                    raise ReleaseContractError(f"release must not contain symlinks: {relative}")
                if source.is_dir():
                    metadata = source.stat(follow_symlinks=False)
                    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o022:
                        raise ReleaseContractError(
                            f"release directory has unsafe custody: {relative}"
                        )
                    (snapshot / relative).mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not source.is_file():
                    raise ReleaseContractError(f"release contains a special filesystem entry: {relative}")
                destination = snapshot / relative
                destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                _copy_stable_file(source, destination)
            yield snapshot
    except OSError as exc:
        raise ReleaseContractError("release payload snapshot failed closed") from exc


def verify_release_payload_unchanged(
    release: Path,
    expected: dict[str, str],
    *,
    excluded: set[str],
) -> None:
    actual_paths: set[str] = set()
    for path in sorted(release.rglob("*")):
        relative = path.relative_to(release).as_posix()
        if path.is_symlink():
            raise ReleaseContractError(f"release must not contain symlinks: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ReleaseContractError(f"release contains a special filesystem entry: {relative}")
        if relative not in excluded:
            actual_paths.add(relative)
    if actual_paths != set(expected):
        raise ReleaseContractError("release payload file set changed during signing")
    for relative, digest in expected.items():
        if _stable_sha256(release / relative) != digest:
            raise ReleaseContractError(f"release payload changed during signing: {relative}")


def write_atomic_file(path: Path, content: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = f".{path.name}.{os.getpid()}.tmp"
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    try:
        parent_fd = os.open(path.parent, directory_flags)
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            descriptor = os.open(temporary_name, flags, 0o600, dir_fd=parent_fd)
            created = True
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
                os.fchmod(handle.fileno(), mode)
            os.replace(
                temporary_name,
                path.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        finally:
            if created:
                with suppress(FileNotFoundError):
                    os.unlink(temporary_name, dir_fd=parent_fd)
            os.close(parent_fd)
    except OSError as exc:
        raise ReleaseContractError(f"cannot write release artifact atomically: {path.name}") from exc


def contains_private_key_material(content: bytes) -> bool:
    """Recognize unencrypted PEM or DER private-key payloads."""
    if b"PRIVATE KEY-----" in content:
        return True
    try:
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        return False
    for loader in (serialization.load_pem_private_key, serialization.load_der_private_key):
        try:
            loader(content, password=None)
        except TypeError:
            return True
        except ValueError:
            continue
        return True
    return False


def _stream_contains_private_key(
    handle: BinaryReader,
    size: int | None = None,
    *,
    budget: dict[str, int] | None = None,
) -> bool:
    marker = b"PRIVATE KEY-----"
    overlap = b""
    collected = bytearray() if size is None or size <= 1024 * 1024 else None
    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
        if budget is not None:
            budget["scanned"] = budget.get("scanned", 0) + len(chunk)
            if budget["scanned"] > 64 * 1024**3:
                raise ReleaseContractError("private-key archive scan exceeds expansion limits")
        if marker in overlap + chunk:
            return True
        overlap = chunk[-(len(marker) - 1):]
        if collected is not None:
            collected.extend(chunk)
            if len(collected) > 1024 * 1024:
                collected = None
    return collected is not None and contains_private_key_material(bytes(collected))


def _looks_like_tar(prefix: bytes) -> bool:
    if len(prefix) >= 262 and prefix[257:262] == b"ustar":
        return True
    if len(prefix) < 512 or not prefix[:100].rstrip(b"\0"):
        return False
    try:
        expected = int(prefix[148:156].rstrip(b"\0 ") or b"0", 8)
    except ValueError:
        return False
    checksum_header = bytearray(prefix[:512])
    checksum_header[148:156] = b"        "
    return sum(checksum_header) == expected


def _scan_stream(
    handle: BinaryReader,
    *,
    size: int | None,
    depth: int,
    budget: dict[str, int],
    require_archive: bool = False,
) -> bool:
    prefix = handle.read(512)
    source = PrefixedReader(prefix, handle)
    if prefix.startswith(b"\x1f\x8b"):
        try:
            compressed = gzip.GzipFile(fileobj=cast(Any, source), mode="rb")
            expanded_prefix = compressed.read(512)
        except (OSError, EOFError) as exc:
            raise ReleaseContractError("private-key archive scan failed closed") from exc
        expanded = PrefixedReader(expanded_prefix, compressed)
        if _looks_like_tar(expanded_prefix):
            return _scan_tar_stream(expanded, depth=depth + 1, budget=budget)
        if require_archive:
            raise ReleaseContractError("private-key archive scan failed closed")
        return _stream_contains_private_key(expanded, budget=budget)
    if require_archive or _looks_like_tar(prefix):
        return _scan_tar_stream(source, depth=depth + 1, budget=budget)
    return _stream_contains_private_key(source, size, budget=budget)


def _scan_tar_stream(
    handle: BinaryReader,
    *,
    depth: int,
    budget: dict[str, int],
) -> bool:
    if depth > 4:
        raise ReleaseContractError("private-key archive scan exceeds nesting limit")
    try:
        with tarfile.open(fileobj=cast(Any, handle), mode="r|*") as archive:
            for member in archive:
                budget["members"] += 1
                budget["bytes"] += member.size
                if budget["members"] > 1_000_000 or budget["bytes"] > 64 * 1024**3:
                    raise ReleaseContractError("private-key archive scan exceeds expansion limits")
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ReleaseContractError("private-key archive member cannot be inspected")
                archive_name = member.name.lower()
                if _scan_stream(
                    extracted,
                    size=member.size,
                    depth=depth,
                    budget=budget,
                    require_archive=archive_name.endswith((".tar", ".tar.gz", ".tgz")),
                ):
                    return True
    except (OSError, tarfile.TarError, EOFError) as exc:
        raise ReleaseContractError("private-key archive scan failed closed") from exc
    return False


def path_contains_private_key_material(path: Path, *, require_archive: bool = False) -> bool:
    """Recursively scan bounded archive members and fail closed on parser errors."""
    try:
        with path.open("rb") as handle:
            return _scan_stream(
                handle,
                size=path.stat().st_size,
                depth=0,
                budget={"members": 0, "bytes": 0, "scanned": 0},
                require_archive=require_archive,
            )
    except OSError as exc:
        raise ReleaseContractError("private-key payload scan failed closed") from exc


def validate_single_image_archive(
    path: Path,
    expected_image: str | None = None,
    *,
    expected_revision: str | None = None,
) -> str:
    """Require one tagless ARM64 image whose config ID is the signed image identity."""

    def read_bytes(
        archive: tarfile.TarFile,
        members: dict[str, tarfile.TarInfo],
        name: str,
        maximum: int,
    ) -> bytes:
        member = members.get(name)
        if member is None or member.size > maximum:
            raise ReleaseContractError(f"offline image archive requires bounded {name}")
        extracted = archive.extractfile(member)
        if extracted is None:
            raise ReleaseContractError(f"offline image archive cannot inspect {name}")
        return extracted.read()

    def read_json(archive: tarfile.TarFile, members: dict[str, tarfile.TarInfo], name: str) -> Any:
        return json.loads(
            read_bytes(archive, members, name, 1024 * 1024).decode("utf-8"),
            object_pairs_hook=_unique_json_object,
        )

    def member_sha256(
        archive: tarfile.TarFile,
        members: dict[str, tarfile.TarInfo],
        name: str,
    ) -> str:
        member = members.get(name)
        extracted = archive.extractfile(member) if member is not None else None
        if extracted is None:
            raise ReleaseContractError(f"offline image archive cannot inspect {name}")
        digest = hashlib.sha256()
        for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()

    def validate_layer(
        archive: tarfile.TarFile,
        members: dict[str, tarfile.TarInfo],
        name: str,
        budget: dict[str, int],
    ) -> None:
        member = members.get(name)
        extracted = archive.extractfile(member) if member is not None else None
        if extracted is None:
            raise ReleaseContractError(f"offline image archive cannot inspect layer: {name}")
        try:
            prefix = extracted.read(6)
            if (
                prefix.startswith(b"\x1f\x8b")
                or prefix.startswith(b"BZh")
                or prefix.startswith(b"\xfd7zXZ\x00")
                or prefix.startswith(b"\x28\xb5\x2f\xfd")
            ):
                raise ReleaseContractError("offline image layer compression does not match its media type")
            stream = PrefixedReader(prefix, extracted)
            with tarfile.open(fileobj=cast(Any, stream), mode="r|") as layer:
                for layer_member in layer:
                    budget["members"] += 1
                    if budget["members"] > MAX_IMAGE_LAYER_MEMBERS:
                        raise ReleaseContractError("offline image layer exceeds the member limit")
                    if layer_member.size < 0:
                        raise ReleaseContractError("offline image layer has an invalid member size")
                    budget["bytes"] += layer_member.size
                    if budget["bytes"] > MAX_IMAGE_EXPANDED_BYTES:
                        raise ReleaseContractError("offline image layer exceeds the expansion limit")
                    member_path = PurePosixPath(layer_member.name)
                    if (
                        member_path.is_absolute()
                        or ".." in member_path.parts
                        or "\\" in layer_member.name
                    ):
                        raise ReleaseContractError("offline image layer contains an unsafe path")
                    if not (
                        layer_member.isfile()
                        or layer_member.isdir()
                        or layer_member.issym()
                        or layer_member.islnk()
                    ):
                        raise ReleaseContractError("offline image layer contains a special member")
                    if layer_member.issym() or layer_member.islnk():
                        link_path = PurePosixPath(layer_member.linkname)
                        parts = [] if link_path.is_absolute() or layer_member.islnk() else list(member_path.parent.parts)
                        for part in link_path.parts:
                            if part in {"", ".", "/"}:
                                continue
                            if part == "..":
                                if not parts:
                                    raise ReleaseContractError("offline image layer link escapes the root")
                                parts.pop()
                            else:
                                parts.append(part)
        except (OSError, tarfile.TarError, EOFError) as exc:
            raise ReleaseContractError("offline image archive contains an invalid layer tar") from exc

    try:
        archive_fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
        try:
            prefix = os.read(archive_fd, 6)
        finally:
            os.close(archive_fd)
        if (
            prefix.startswith(b"\x1f\x8b")
            or prefix.startswith(b"BZh")
            or prefix.startswith(b"\xfd7zXZ\x00")
        ):
            raise ReleaseContractError("offline image outer archive must be an uncompressed tar")
        with tarfile.open(path, mode="r:") as archive:
            members: dict[str, tarfile.TarInfo] = {}
            expanded_bytes = 0
            for count, member in enumerate(archive, 1):
                if count > 100_000:
                    raise ReleaseContractError("offline image archive exceeds the member limit")
                if member.size < 0:
                    raise ReleaseContractError("offline image archive has an invalid member size")
                expanded_bytes += member.size
                if expanded_bytes > 64 * 1024**3:
                    raise ReleaseContractError("offline image archive exceeds the expansion limit")
                member_path = PurePosixPath(member.name)
                if (
                    member_path.is_absolute()
                    or member_path.as_posix() != member.name
                    or ".." in member_path.parts
                    or "\\" in member.name
                ):
                    raise ReleaseContractError("offline image archive contains an unsafe member path")
                if member.isdir():
                    continue
                if not member.isfile():
                    raise ReleaseContractError("offline image archive contains a special member")
                if member.name in members:
                    raise ReleaseContractError("offline image archive contains duplicate member paths")
                members[member.name] = member
            manifest = read_json(archive, members, "manifest.json")
            if not isinstance(manifest, list) or len(manifest) != 1 or not isinstance(manifest[0], dict):
                raise ReleaseContractError("offline image archive must contain exactly one image")
            record = manifest[0]
            config_name = record.get("Config")
            tags = record.get("RepoTags")
            layers = record.get("Layers")
            if (
                not isinstance(config_name, str)
                or config_name not in members
                or tags not in (None, [])
                or not isinstance(layers, list)
                or not layers
                or any(not isinstance(layer, str) or layer not in members for layer in layers)
                or set(record) not in (
                    {"Config", "RepoTags", "Layers"},
                    {"Config", "RepoTags", "Layers", "LayerSources"},
                )
            ):
                raise ReleaseContractError("offline image archive inventory is invalid")
            layer_sources = record.get("LayerSources")
            if layer_sources is not None:
                expected_sources = {
                    f"sha256:{PurePosixPath(layer_name).name}": layer_name
                    for layer_name in layers
                }
                if not isinstance(layer_sources, dict) or set(layer_sources) != set(expected_sources):
                    raise ReleaseContractError("offline image layer-source inventory is invalid")
                for digest, descriptor in layer_sources.items():
                    layer_name = expected_sources[digest]
                    layer_member = members[layer_name]
                    layer_digest = PurePosixPath(layer_name).name
                    if (
                        not layer_name.startswith("blobs/sha256/")
                        or not HEX_SHA256.fullmatch(layer_digest)
                        or not isinstance(descriptor, dict)
                        or descriptor != {
                            "digest": digest,
                            "mediaType": "application/vnd.oci.image.layer.v1.tar",
                            "size": layer_member.size,
                        }
                    ):
                        raise ReleaseContractError("offline image layer-source descriptor is invalid")
            config_id = PurePosixPath(config_name).name.removesuffix(".json").lower()
            if not HEX_SHA256.fullmatch(config_id):
                raise ReleaseContractError("offline image archive config identity is invalid")
            if expected_image is not None and expected_image != f"sha256:{config_id}":
                raise ReleaseContractError("offline image archive does not match its signed image ID")
            config_bytes = read_bytes(archive, members, config_name, 1024 * 1024)
            if hashlib.sha256(config_bytes).hexdigest() != config_id:
                raise ReleaseContractError("offline image config content digest is invalid")
            config = json.loads(config_bytes.decode("utf-8"), object_pairs_hook=_unique_json_object)
            if not isinstance(config, dict) or config.get("architecture") != "arm64" or config.get("os") != "linux":
                raise ReleaseContractError("offline image archive is not a Linux ARM64 image")
            if expected_revision is not None:
                runtime_config = config.get("config")
                labels = runtime_config.get("Labels") if isinstance(runtime_config, dict) else None
                source = labels.get("org.opencontainers.image.source") if isinstance(labels, dict) else None
                version = labels.get("org.opencontainers.image.version") if isinstance(labels, dict) else None
                revision = labels.get("org.opencontainers.image.revision") if isinstance(labels, dict) else None
                source_url = urlsplit(source) if isinstance(source, str) else None
                if (
                    revision != expected_revision
                    or source_url is None
                    or source_url.scheme != "https"
                    or not source_url.hostname
                    or source_url.username is not None
                    or source_url.password is not None
                    or source_url.query
                    or source_url.fragment
                    or not isinstance(version, str)
                    or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version)
                ):
                    raise ReleaseContractError(
                        "offline image provenance labels do not match the gated source revision"
                    )
            layer_digests: list[str] = []
            layer_budget = {"members": 0, "bytes": 0}
            for layer_name in layers:
                layer_digest = member_sha256(archive, members, layer_name)
                layer_digests.append(f"sha256:{layer_digest}")
                if layer_name.startswith("blobs/sha256/"):
                    expected_layer = PurePosixPath(layer_name).name
                    if layer_digest != expected_layer:
                        raise ReleaseContractError("offline image layer content digest is invalid")
                validate_layer(archive, members, layer_name, layer_budget)
            rootfs = config.get("rootfs")
            if (
                not isinstance(rootfs, dict)
                or rootfs.get("type") != "layers"
                or rootfs.get("diff_ids") != layer_digests
            ):
                raise ReleaseContractError("offline image config does not bind the exact layer contents")
            referenced_members = {"manifest.json", config_name, *layers}
            if "index.json" in members:
                if config_name != f"blobs/sha256/{config_id}":
                    raise ReleaseContractError("offline OCI config must use its canonical blob path")
                layout = read_json(archive, members, "oci-layout")
                if layout != {"imageLayoutVersion": "1.0.0"}:
                    raise ReleaseContractError("offline OCI layout metadata is invalid")
                index = read_json(archive, members, "index.json")
                descriptors = index.get("manifests") if isinstance(index, dict) else None
                if (
                    not isinstance(index, dict)
                    or index.get("schemaVersion") != 2
                    or index.get("mediaType") != "application/vnd.oci.image.index.v1+json"
                ):
                    raise ReleaseContractError("offline OCI index contract is invalid")
                if not isinstance(descriptors, list) or len(descriptors) != 1 or not isinstance(descriptors[0], dict):
                    raise ReleaseContractError("offline OCI index must contain exactly one image manifest")
                descriptor = descriptors[0]
                digest = descriptor.get("digest")
                if (
                    not isinstance(digest, str)
                    or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
                    or descriptor.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
                    or not isinstance(descriptor.get("size"), int)
                    or descriptor.get("platform")
                    not in (None, {"architecture": "arm64", "os": "linux"})
                ):
                    raise ReleaseContractError("offline OCI index manifest digest is invalid")
                manifest_name = f"blobs/sha256/{digest.removeprefix('sha256:')}"
                manifest_bytes = read_bytes(archive, members, manifest_name, 1024 * 1024)
                if (
                    hashlib.sha256(manifest_bytes).hexdigest() != digest.removeprefix("sha256:")
                    or descriptor["size"] != len(manifest_bytes)
                ):
                    raise ReleaseContractError("offline OCI manifest content digest is invalid")
                oci_manifest = json.loads(
                    manifest_bytes.decode("utf-8"),
                    object_pairs_hook=_unique_json_object,
                )
                if (
                    not isinstance(oci_manifest, dict)
                    or oci_manifest.get("schemaVersion") != 2
                    or oci_manifest.get("mediaType")
                    != "application/vnd.oci.image.manifest.v1+json"
                ):
                    raise ReleaseContractError("offline OCI image manifest contract is invalid")
                oci_config = oci_manifest.get("config") if isinstance(oci_manifest, dict) else None
                if (
                    not isinstance(oci_config, dict)
                    or oci_config.get("mediaType") != "application/vnd.oci.image.config.v1+json"
                    or oci_config.get("digest") != f"sha256:{config_id}"
                    or oci_config.get("size") != len(config_bytes)
                ):
                    raise ReleaseContractError("offline OCI manifest config identity is invalid")
                oci_layers = oci_manifest.get("layers") if isinstance(oci_manifest, dict) else None
                if not isinstance(oci_layers, list) or len(oci_layers) != len(layers):
                    raise ReleaseContractError("offline OCI manifest layer inventory is invalid")
                oci_layer_names: list[str] = []
                for layer_descriptor in oci_layers:
                    if (
                        not isinstance(layer_descriptor, dict)
                        or layer_descriptor.get("mediaType") != "application/vnd.oci.image.layer.v1.tar"
                        or not isinstance(layer_descriptor.get("digest"), str)
                        or not re.fullmatch(r"sha256:[0-9a-f]{64}", layer_descriptor["digest"])
                        or not isinstance(layer_descriptor.get("size"), int)
                    ):
                        raise ReleaseContractError("offline OCI layer descriptor is unsupported")
                    layer_name = f"blobs/sha256/{layer_descriptor['digest'].removeprefix('sha256:')}"
                    oci_member = members.get(layer_name)
                    if oci_member is None or layer_descriptor["size"] != oci_member.size:
                        raise ReleaseContractError("offline OCI layer descriptor size is invalid")
                    oci_layer_names.append(layer_name)
                if oci_layer_names != layers:
                    raise ReleaseContractError("offline OCI and Docker layer inventories disagree")
                referenced_members.update({"index.json", "oci-layout", manifest_name})
                legacy_fields = {"architecture", "config", "container_config", "created", "id", "os"}
                for extra_name in set(members) - referenced_members:
                    if (
                        not extra_name.startswith("blobs/sha256/")
                        or not HEX_SHA256.fullmatch(PurePosixPath(extra_name).name)
                        or member_sha256(archive, members, extra_name) != PurePosixPath(extra_name).name
                    ):
                        raise ReleaseContractError("offline image archive contains an unreferenced member")
                    legacy = read_json(archive, members, extra_name)
                    if not isinstance(legacy, dict) or set(legacy) != legacy_fields:
                        raise ReleaseContractError("offline image archive contains unsupported metadata")
            else:
                allowed_companions: set[str] = set()
                for layer_name in layers:
                    if layer_name.endswith("/layer.tar"):
                        parent = PurePosixPath(layer_name).parent.as_posix()
                        allowed_companions.update({f"{parent}/json", f"{parent}/VERSION"})
                extras = set(members) - referenced_members
                if not extras.issubset(allowed_companions):
                    raise ReleaseContractError("offline image archive contains an unreferenced member")
                for extra_name in extras:
                    if extra_name.endswith("/VERSION"):
                        if read_bytes(archive, members, extra_name, 16).strip() != b"1.0":
                            raise ReleaseContractError("offline classic image VERSION metadata is invalid")
                    else:
                        legacy = read_json(archive, members, extra_name)
                        parent = PurePosixPath(extra_name).parent.name
                        if not isinstance(legacy, dict) or legacy.get("id") != parent:
                            raise ReleaseContractError("offline classic image metadata is invalid")
    except (OSError, UnicodeError, json.JSONDecodeError, tarfile.TarError) as exc:
        raise ReleaseContractError("offline image archive inventory scan failed closed") from exc
    return f"sha256:{config_id}"


def _is_safe_secret_value(value: Any) -> bool:
    if value is None or value is False:
        return True
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    return normalized.lower() in {"", "null", "none", "false", "***", "[redacted]"} or bool(
        re.fullmatch(r"\$\{[A-Z_][A-Z0-9_]*\}", normalized)
    )


def _validate_url(value: str, relative: str, *, reject_camera_url: bool) -> None:
    secret_key = re.compile(r"(?i)(?:password|secret|token|api[_-]?key|capture[_-]?key)")

    def decoded_component(component: str) -> str:
        for _ in range(8):
            decoded = unquote_plus(component)
            if decoded == component:
                return decoded
            component = decoded
        if re.search(r"(?i)%[0-9a-f]{2}", component):
            raise ReleaseContractError(f"release payload contains an over-encoded URL: {relative}")
        return component

    for match in re.finditer(r"(?i)\b(?:https?|rtsps?)://[^\s'\"<>]+", value):
        raw = match.group(0).rstrip(",.)]}")
        parsed = urlsplit(raw)
        if parsed.username or parsed.password:
            raise ReleaseContractError(f"release payload contains a credential-bearing URL: {relative}")
        for query_item in parsed.query.split("&"):
            query_key, separator, query_value = query_item.partition("=")
            if separator and secret_key.search(decoded_component(query_key)) and query_value:
                raise ReleaseContractError(f"release payload contains a credential-bearing URL: {relative}")
        if parsed.fragment and secret_key.search(decoded_component(parsed.fragment)):
            raise ReleaseContractError(f"release payload contains a credential-bearing URL: {relative}")
        if reject_camera_url and parsed.scheme.lower() in {"rtsp", "rtsps"}:
            raise ReleaseContractError("release defaults must not contain customer RTSP endpoints")


def _validate_structured_payload(value: Any, relative: str, *, reject_camera_url: bool) -> None:
    secret_key = re.compile(r"(?i)(?:password|secret|token|api[_-]?key|capture[_-]?key)")
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and secret_key.search(key) and not _is_safe_secret_value(item):
                raise ReleaseContractError(f"release payload contains a secret assignment: {relative}")
            _validate_structured_payload(item, relative, reject_camera_url=reject_camera_url)
    elif isinstance(value, list):
        for item in value:
            _validate_structured_payload(item, relative, reject_camera_url=reject_camera_url)
    elif isinstance(value, str):
        _validate_url(value, relative, reject_camera_url=reject_camera_url)


def validate_release_payload_safety(path: Path, relative: str) -> None:
    """Reject site-specific secrets and customer endpoints from signed media."""
    lower_name = path.name.lower()
    lower_relative = relative.lower()
    if (
        lower_name.startswith(".env")
        or lower_name in {"site.env", "ksi-cctv.env"}
        or path.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".pub"}
        or any(
            marker in lower_relative
            for marker in (
                "release-signing",
                "ci-attestation",
                "backup-recipient",
                "backup-signing",
                "appliance-id",
            )
        )
    ):
        raise ReleaseContractError(f"release payload contains forbidden site/trust material: {relative}")
    size = path.stat().st_size
    if relative.startswith("images/") and relative.endswith(".tar"):
        return
    if size > MAX_TEXT_PAYLOAD_BYTES:
        raise ReleaseContractError(f"release text payload exceeds the inspection limit: {relative}")
    try:
        content = path.read_bytes()
        text = content.decode("utf-16" if content.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseContractError(f"release payload is not inspectable UTF text: {relative}") from exc
    reject_camera_url = relative.startswith("defaults/config/")
    structured: Any = None
    if path.suffix.lower() == ".json":
        try:
            structured = json.loads(text, object_pairs_hook=_unique_json_object)
        except json.JSONDecodeError as exc:
            raise ReleaseContractError(f"invalid JSON release artifact: {relative}") from exc
        if relative == "defaults/config/cameras.json" and structured != []:
            raise ReleaseContractError("release payload must not contain customer camera records")
        _validate_structured_payload(structured, relative, reject_camera_url=reject_camera_url)
    _validate_url(text, relative, reject_camera_url=reject_camera_url)
    assignment = re.compile(
        r"(?i)(?:^|[\s,{;])['\"]?"
        r"[A-Za-z0-9_-]*(?:password|secret|token|api[_-]?key|capture[_-]?key)[A-Za-z0-9_-]*"
        r"['\"]?\s*[:=]\s*"
        r"(?P<value>\$\{[^}\r\n]+\}|\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s,;}\]]*)"
    )
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or (stripped.startswith("#") and "=" not in stripped):
            continue
        for match in assignment.finditer(stripped):
            normalized = match.group("value").strip().strip("'\"")
            if not _is_safe_secret_value(normalized) and not normalized.startswith(("<", "REPLACE_")):
                raise ReleaseContractError(f"release payload contains a secret assignment: {relative}")


def _safe_relative(value: Any, *, suffix: str | None = None) -> str:
    if not isinstance(value, str) or not value:
        raise ReleaseContractError("release contract path must be a non-empty string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or "\\" in value
        or ".." in path.parts
        or (suffix and not value.endswith(suffix))
    ):
        raise ReleaseContractError(f"unsafe release contract path: {value}")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseContractError(f"JSON release artifact contains a duplicate key: {key}")
        result[key] = value
    return result


def _load_json(release: Path, relative: str, *, canonical: bool = False) -> dict[str, Any]:
    path = release / _safe_relative(relative)
    try:
        raw = read_stable_file_bytes(path, maximum=16 * 1024 * 1024)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"invalid JSON release artifact: {relative}") from exc
    if not isinstance(value, dict):
        raise ReleaseContractError(f"release artifact must be a JSON object: {relative}")
    if canonical and raw != canonical_json(value) + b"\n":
        raise ReleaseContractError(f"release artifact is not canonical JSON: {relative}")
    return value


def load_release_contract(release: Path) -> dict[str, Any]:
    return _load_json(release, CONTRACT_PATH)


def validate_persistent_state(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("migration_mode") != "none":
        raise ReleaseContractError("v1 persistent_state requires migration_mode=none")
    stores = value.get("stores")
    if not isinstance(stores, dict) or set(stores) != set(STORE_SCHEMAS):
        raise ReleaseContractError("persistent_state must declare every supported store exactly")
    for name, expected in STORE_SCHEMAS.items():
        item = stores.get(name)
        if not isinstance(item, dict) or item.get("schema") != expected:
            raise ReleaseContractError(f"persistent_state schema mismatch for {name}")
        readable = item.get("rollback_readable_versions")
        if not isinstance(readable, list) or readable != [expected]:
            raise ReleaseContractError(f"persistent_state rollback readers must be explicit for {name}")
    return value


def persistent_contract_compatible(current: Any, candidate: Any) -> bool:
    """Return whether candidate and current code can read the same data without migration."""
    try:
        current_value = validate_persistent_state(current)
        candidate_value = validate_persistent_state(candidate)
    except ReleaseContractError:
        return False
    for name in STORE_SCHEMAS:
        current_schema = current_value["stores"][name]["schema"]
        candidate_schema = candidate_value["stores"][name]["schema"]
        if current_schema not in candidate_value["stores"][name]["rollback_readable_versions"]:
            return False
        if candidate_schema not in current_value["stores"][name]["rollback_readable_versions"]:
            return False
    return True


def _validate_spdx(release: Path, relative: str, image: str) -> None:
    value = _load_json(release, relative)
    if value.get("spdxVersion") != "SPDX-2.3" or value.get("SPDXID") != "SPDXRef-DOCUMENT":
        raise ReleaseContractError(f"SBOM is not SPDX 2.3: {relative}")
    if not isinstance(value.get("name"), str) or not value.get("name"):
        raise ReleaseContractError(f"SBOM name is missing: {relative}")
    if (
        not isinstance(value.get("documentNamespace"), str)
        or not value["documentNamespace"].strip()
        or PLACEHOLDER.search(value["documentNamespace"])
    ):
        raise ReleaseContractError(f"SBOM namespace is missing or a placeholder: {relative}")
    creation = value.get("creationInfo")
    if (
        value.get("dataLicense") != "CC0-1.0"
        or not isinstance(creation, dict)
        or not isinstance(creation.get("created"), str)
        or not creation["created"].endswith("Z")
        or not isinstance(creation.get("creators"), list)
        or not creation["creators"]
    ):
        raise ReleaseContractError(f"SBOM creation metadata is incomplete: {relative}")
    packages = value.get("packages")
    describes = value.get("documentDescribes")
    if not isinstance(packages, list) or not packages or not isinstance(describes, list) or not describes:
        raise ReleaseContractError(f"SBOM must describe at least one package: {relative}")
    if (
        any(not isinstance(item, str) or not SPDX_REFERENCE.fullmatch(item) for item in describes)
        or len(set(describes)) != len(describes)
    ):
        raise ReleaseContractError(f"SBOM documentDescribes references are invalid: {relative}")
    image_digest = image.rsplit("sha256:", 1)[-1].lower()
    linked = False
    package_ids: set[str] = set()
    for package in packages:
        package_id = package.get("SPDXID") if isinstance(package, dict) else None
        if (
            not isinstance(package, dict)
            or not isinstance(package_id, str)
            or not SPDX_REFERENCE.fullmatch(package_id)
            or package_id == "SPDXRef-DOCUMENT"
            or package_id in package_ids
            or not isinstance(package.get("name"), str)
            or not package["name"].strip()
            or not isinstance(package.get("downloadLocation"), str)
            or not package["downloadLocation"].strip()
            or package.get("filesAnalyzed") is not False
            or not _approved_license(package.get("licenseConcluded"))
            or not _approved_license(package.get("licenseDeclared"))
            or not isinstance(package.get("copyrightText"), str)
            or not package["copyrightText"].strip()
        ):
            raise ReleaseContractError(f"SBOM package identity is invalid: {relative}")
        package_ids.add(package_id)
        checksums = package.get("checksums")
        if package_id in describes and isinstance(checksums, list):
            linked = linked or any(
                isinstance(checksum, dict)
                and str(checksum.get("algorithm", "")).upper() == "SHA256"
                and str(checksum.get("checksumValue", "")).lower() == image_digest
                for checksum in checksums
            )
    if not set(describes).issubset(package_ids):
        raise ReleaseContractError(f"SBOM documentDescribes references an unknown package: {relative}")
    if not linked:
        raise ReleaseContractError(f"SBOM is not linked to its exact image digest: {relative}")


def _approved_license(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value.strip()
        or PLACEHOLDER.search(value)
    ):
        return False
    try:
        SPDX_LICENSING.parse(value, validate=True, strict=True)
    except (ExpressionError, ValueError):
        return False
    return True


def _validate_model_inventory(
    release: Path,
    relative: str,
    engine_image: str,
    notices_path: str,
) -> dict[str, dict[str, Any]]:
    value = _load_json(release, relative)
    models = value.get("models")
    if value.get("schema_version") != 1 or not isinstance(models, list) or not models:
        raise ReleaseContractError("model inventory must contain at least one schema-v1 model")
    identifiers: set[str] = set()
    model_paths: set[str] = set()
    result: dict[str, dict[str, Any]] = {}
    notices = (release / notices_path).read_text(encoding="utf-8")
    for model in models:
        if not isinstance(model, dict):
            raise ReleaseContractError("model inventory entries must be objects")
        identifier = model.get("id")
        if not isinstance(identifier, str) or not identifier or identifier in identifiers:
            raise ReleaseContractError("model inventory IDs must be unique non-empty strings")
        identifiers.add(identifier)
        if model.get("image") != engine_image:
            raise ReleaseContractError(f"model inventory image is not the engine image: {identifier}")
        if not isinstance(model.get("path"), str) or not model["path"].startswith("/app/models/"):
            raise ReleaseContractError(f"model inventory path is not an engine image path: {identifier}")
        model_path = PurePosixPath(model["path"])
        if (
            model_path.as_posix() != model["path"]
            or "\\" in model["path"]
            or ".." in model_path.parts
            or model_path.suffix.lower() not in {".onnx", ".pt", ".engine"}
            or model_path.as_posix() in model_paths
        ):
            raise ReleaseContractError(f"model inventory path must be unique and executable: {identifier}")
        model_paths.add(model_path.as_posix())
        if not isinstance(model.get("sha256"), str) or not HEX_SHA256.fullmatch(model["sha256"]):
            raise ReleaseContractError(f"model inventory SHA-256 is invalid: {identifier}")
        for field in ("source_url", "source_revision", "notice", "approval_reference"):
            field_value = model.get(field)
            if not isinstance(field_value, str) or not field_value.strip() or PLACEHOLDER.search(field_value):
                raise ReleaseContractError(f"model inventory {field} is missing or a placeholder: {identifier}")
        if not str(model["source_url"]).startswith("https://") or not COMMIT.fullmatch(str(model["source_revision"])):
            raise ReleaseContractError(f"model inventory source is not immutable HTTPS: {identifier}")
        notice_prefix = f"{notices_path}#"
        if not str(model["notice"]).startswith(notice_prefix):
            raise ReleaseContractError(f"model inventory notice does not reference THIRD_PARTY_NOTICES: {identifier}")
        notice_section = str(model["notice"])[len(notice_prefix):]
        if not notice_section or f"[{notice_section}]" not in notices:
            raise ReleaseContractError(f"model inventory notice section is missing: {identifier}")
        for field in ("code_license", "weight_license", "dataset_license"):
            if not _approved_license(model.get(field)):
                raise ReleaseContractError(f"model inventory {field} is unapproved: {identifier}")
        result[str(identifier)] = model
    return result


def _validate_license_approvals(
    release: Path,
    relative: str,
    commit: str,
    models: dict[str, dict[str, Any]],
) -> None:
    value = _load_json(release, relative)
    approvals = value.get("models")
    if value.get("schema_version") != 1 or value.get("source_commit") != commit:
        raise ReleaseContractError("license approvals must be schema v1 for the release commit")
    if not isinstance(approvals, dict) or set(approvals) != set(models):
        raise ReleaseContractError("license approvals must cover every model exactly")
    for identifier, model in models.items():
        approval = approvals.get(identifier)
        if not isinstance(approval, dict) or set(approval) != {
            "status", "approval_reference", "model_sha256", "code_license",
            "weight_license", "dataset_license",
        }:
            raise ReleaseContractError(f"license approval fields are invalid: {identifier}")
        if approval.get("status") != "approved" or any(
            approval.get(field) != model.get(field)
            for field in (
                "approval_reference", "code_license", "weight_license", "dataset_license"
            )
        ) or approval.get("model_sha256") != model.get("sha256"):
            raise ReleaseContractError(f"license approval does not match model inventory: {identifier}")


def _load_attestation(release: Path, source: dict[str, Any]) -> dict[str, Any]:
    relative = _safe_relative(source.get("gate_attestation"), suffix=".json")
    value = _load_json(release, relative, canonical=True)
    required = {
        "schema_version", "strict", "passed", "commit", "clean_tree",
        "tracked_tree_sha256", "release_script_sha256", "key_lock_digests",
        "config_digests", "command", "tool_versions", "started_at_utc",
        "ended_at_utc", "runner", "evidence_sha256",
    }
    if set(value) != required:
        raise ReleaseContractError("software gate attestation schema is incomplete or unexpected")
    if value.get("schema_version") != 1 or value.get("strict") is not True:
        raise ReleaseContractError("software gate attestation is not strict schema v1")
    if value.get("passed") is not True or value.get("clean_tree") is not True:
        raise ReleaseContractError("software gate attestation did not pass on a clean tree")
    if value.get("commit") != source.get("commit"):
        raise ReleaseContractError("software gate attestation commit mismatch")
    for field in ("tracked_tree_sha256", "release_script_sha256"):
        if not isinstance(value.get(field), str) or not HEX_SHA256.fullmatch(value[field]):
            raise ReleaseContractError(f"software gate attestation {field} is invalid")
    for field in ("key_lock_digests", "config_digests"):
        mapping = value.get(field)
        if not isinstance(mapping, dict) or not mapping or any(
            not isinstance(name, str)
            or not name
            or not isinstance(digest, str)
            or not HEX_SHA256.fullmatch(digest)
            for name, digest in mapping.items()
        ):
            raise ReleaseContractError(f"software gate attestation {field} is invalid")
    tools = value.get("tool_versions")
    if not isinstance(tools, dict) or set(tools) != TOOL_VERSION_KEYS or not all(
        isinstance(version, str) and bool(version.strip()) for version in tools.values()
    ):
        raise ReleaseContractError("software gate attestation tool versions are invalid")
    if value.get("command") != STRICT_GATE_COMMAND:
        raise ReleaseContractError("software gate attestation command is invalid")
    if not isinstance(value.get("runner"), str) or not RUNNER.fullmatch(value["runner"]):
        raise ReleaseContractError("software gate attestation runner is invalid")
    for field in ("started_at_utc", "ended_at_utc"):
        timestamp = value.get(field)
        if not isinstance(timestamp, str) or not timestamp.endswith("Z"):
            raise ReleaseContractError(f"software gate attestation {field} is invalid")
    evidence = value.get("evidence_sha256")
    if not isinstance(evidence, dict) or set(evidence) != EVIDENCE_KINDS:
        raise ReleaseContractError("software gate attestation evidence is incomplete")
    return value


def _validate_attestation_evidence(
    release: Path,
    source: dict[str, Any],
    attestation: dict[str, Any],
    files: set[str],
) -> set[str]:
    attestation_parent = Path(_safe_relative(source["gate_attestation"])).parent
    evidence_paths: set[str] = set()
    owners: dict[str, str] = {}
    for kind, mapping in attestation["evidence_sha256"].items():
        expected_name = EVIDENCE_SUMMARY_PATHS[kind]
        if not isinstance(mapping, dict) or set(mapping) != {expected_name}:
            raise ReleaseContractError("software gate attestation evidence mappings are not canonical")
        for name, expected in mapping.items():
            relative = _safe_relative(name)
            if not isinstance(expected, str) or not HEX_SHA256.fullmatch(expected):
                raise ReleaseContractError("software gate attestation evidence digest is invalid")
            bundled = (attestation_parent / relative).as_posix()
            prior_kind = owners.setdefault(bundled, kind)
            if prior_kind != kind:
                raise ReleaseContractError("software gate evidence kinds must use distinct paths")
            candidate = release / bundled
            if bundled not in files or not candidate.is_file():
                raise ReleaseContractError(f"software gate evidence is unsigned or missing: {bundled}")
            validate_gate_evidence_summary(candidate, kind)
            digest = hashlib.sha256(read_stable_file_bytes(candidate, maximum=1024 * 1024)).hexdigest()
            if digest != expected:
                raise ReleaseContractError(f"software gate evidence digest mismatch: {bundled}")
            evidence_paths.add(bundled)
    return evidence_paths


def validate_release_payload_layout(
    release: Path,
    contract: dict[str, Any],
    files: set[str],
    image_archives: dict[str, str],
) -> set[str]:
    """Allow only runtime, legal, image, and exact gate-evidence payload paths."""
    source = contract.get("source")
    sboms = contract.get("sboms")
    if not isinstance(source, dict) or not isinstance(sboms, dict):
        raise ReleaseContractError("release contract source or SBOM mapping is missing")
    evidence_paths = _validate_attestation_evidence(
        release,
        source,
        _load_attestation(release, source),
        files,
    )
    allowed = {
        CONTRACT_PATH,
        *RUNTIME_PAYLOAD_PATHS,
        *DEFAULT_CONFIG_PATHS,
        *evidence_paths,
        *(_safe_relative(path, suffix=".tar") for path in image_archives.values()),
        *(_safe_relative(path, suffix=".spdx.json") for path in sboms.values()),
        *(
            _safe_relative(source.get(field), suffix=suffix)
            for field, suffix in (
                ("gate_attestation", ".json"),
                ("gate_signature", ".sig"),
                ("bundle_attestation", ".json"),
                ("bundle_signature", ".sig"),
            )
        ),
        _safe_relative(contract.get("third_party_notices")),
        _safe_relative(contract.get("model_inventory"), suffix=".json"),
        _safe_relative(contract.get("license_approvals"), suffix=".json"),
    }
    unexpected = files - allowed
    if unexpected:
        raise ReleaseContractError(
            f"release payload contains an unapproved path: {sorted(unexpected)[0]}"
        )
    missing_defaults = DEFAULT_CONFIG_PATHS - files
    if missing_defaults:
        raise ReleaseContractError(
            f"release payload is missing a required runtime default: {sorted(missing_defaults)[0]}"
        )
    return evidence_paths


def _load_ed25519_public_key(path: Path) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        data = read_stable_file_bytes(
            path,
            maximum=64 * 1024,
            owner_uid=os.geteuid(),
            reject_writable_by_others=True,
            safe_ancestors=True,
        )
        try:
            key = serialization.load_pem_public_key(data)
        except ValueError:
            key = Ed25519PublicKey.from_public_bytes(base64.b64decode(data.strip(), validate=True))
    except ReleaseContractError:
        raise
    except (ImportError, ValueError, TypeError) as exc:
        raise ReleaseContractError("trusted CI Ed25519 public key is required") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise ReleaseContractError("trusted CI key is not Ed25519")
    return key


def load_external_ed25519_public_key(release: Path, path: Path) -> Any:
    try:
        path.resolve(strict=True).relative_to(release.resolve(strict=True))
    except ValueError:
        return _load_ed25519_public_key(path)
    except OSError as exc:
        raise ReleaseContractError("trusted CI Ed25519 public key is required") from exc
    raise ReleaseContractError("trusted Ed25519 public key must be outside the release")


def ed25519_public_bytes(path: Path) -> bytes:
    from cryptography.hazmat.primitives import serialization

    return _load_ed25519_public_key(path).public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def validate_gate_evidence_summary(path: Path, kind: str) -> dict[str, Any]:
    expected_name = EVIDENCE_SUMMARY_PATHS.get(kind)
    if expected_name is None or path.name != expected_name:
        raise ReleaseContractError("software gate evidence does not use its canonical summary path")
    try:
        raw = read_stable_file_bytes(path, maximum=1024 * 1024)
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseContractError(f"software gate {kind} summary is invalid") from exc
    if not isinstance(value, dict) or raw != canonical_json(value) + b"\n":
        raise ReleaseContractError(f"software gate {kind} summary is not canonical JSON")
    if kind == "pytest":
        expected_fields = {"schema_version", "tests", "passed", "failed", "errors", "skipped"}
        if (
            set(value) != expected_fields
            or value.get("schema_version") != 1
            or any(
                not isinstance(value.get(field), int)
                or isinstance(value[field], bool)
                or value[field] < 0
                for field in expected_fields - {"schema_version"}
            )
            or value["tests"] < 1
            or value["failed"] != 0
            or value["errors"] != 0
            or value["skipped"] != len(APPROVED_SKIPS)
            or value["passed"] + value["skipped"] != value["tests"]
        ):
            raise ReleaseContractError("software gate pytest summary is not a clean complete run")
    elif kind == "skip":
        expected_records = [
            {"approved": True, "reason": reason, "test": test}
            for test, reason in APPROVED_SKIPS
        ]
        if value != {
            "approved_count": len(APPROVED_SKIPS),
            "missing": [],
            "records": expected_records,
            "reused": [],
            "skip_count": len(APPROVED_SKIPS),
            "unexpected": [],
        }:
            raise ReleaseContractError("software gate skip summary is not the exact approved set")
    elif kind == "pip":
        if value != {"blocked": []}:
            raise ReleaseContractError("software gate pip audit summary contains findings")
    elif kind == "npm":
        if value != {"blocked": [], "maximum_allowed": "high"}:
            raise ReleaseContractError("software gate npm audit summary contains findings")
    elif kind == "playwright" and (
        value.get("schema_version") != 1
        or set(value) != {"schema_version", "passed", "skipped"}
        or not isinstance(value.get("passed"), int)
        or isinstance(value["passed"], bool)
        or value["passed"] < 1
        or value.get("skipped") != 0
    ):
        raise ReleaseContractError("software gate Playwright summary is not a clean complete run")
    return value


def bundle_attestation_payload(
    contract: dict[str, Any],
    version: str,
    profiles: list[str],
    image_archives: dict[str, str],
    files: dict[str, str],
) -> dict[str, Any]:
    if not profiles or len(profiles) != len(set(profiles)):
        raise ReleaseContractError("bundle hardware profiles must be non-empty and unique")
    source = contract.get("source")
    if not isinstance(source, dict):
        raise ReleaseContractError("release contract source is missing")
    excluded = {
        _safe_relative(source.get("bundle_attestation"), suffix=".json"),
        _safe_relative(source.get("bundle_signature"), suffix=".sig"),
    }
    payload_files = {
        name: digest.lower()
        for name, digest in sorted(files.items())
        if name not in excluded
    }
    images: dict[str, dict[str, str]] = {}
    for image, archive in sorted(image_archives.items()):
        digest = files.get(archive)
        if not isinstance(digest, str) or not HEX_SHA256.fullmatch(digest.lower()):
            raise ReleaseContractError(f"bundle image archive checksum is missing: {archive}")
        images[image] = {"archive": archive, "sha256": digest.lower()}
    contract_digest = payload_files.get(CONTRACT_PATH)
    if not isinstance(contract_digest, str) or not HEX_SHA256.fullmatch(contract_digest):
        raise ReleaseContractError("bundle release contract checksum is missing")
    return {
        "schema_version": 2,
        "source_commit": source.get("commit"),
        "version": version,
        "hardware_profiles": sorted(profiles),
        "images": images,
        "payload_sha256": payload_files,
        "release_contract_sha256": contract_digest,
        "release_contract": contract,
    }


def verify_bundle_attestation(
    release: Path,
    contract: dict[str, Any],
    version: str,
    profiles: list[str],
    image_archives: dict[str, str],
    files: dict[str, str],
    trusted_public_key: Any | None,
    *,
    allow_untrusted: bool = False,
) -> None:
    source = contract["source"]
    relative = _safe_relative(source.get("bundle_attestation"), suffix=".json")
    statement = _load_json(release, relative, canonical=True)
    expected = bundle_attestation_payload(contract, version, profiles, image_archives, files)
    if statement != expected:
        raise ReleaseContractError("CI bundle attestation does not match the exact release payload")
    signature_path = release / _safe_relative(source.get("bundle_signature"), suffix=".sig")
    if trusted_public_key is None:
        if allow_untrusted:
            return
        raise ReleaseContractError("an external trusted CI attestation key is required")
    try:
        signature_bytes = read_stable_file_bytes(signature_path, maximum=1024)
        signature = base64.b64decode(signature_bytes.strip(), validate=True)
        if signature_bytes != base64.b64encode(signature) + b"\n":
            raise ValueError("noncanonical bundle signature")
        trusted_public_key.verify(signature, canonical_json(statement))
    except (OSError, ValueError) as exc:
        raise ReleaseContractError("CI bundle attestation signature is invalid") from exc
    except Exception as exc:
        if exc.__class__.__name__ == "InvalidSignature":
            raise ReleaseContractError("CI bundle attestation signature does not verify") from exc
        raise


def verify_attestation_signature(
    release: Path,
    source: dict[str, Any],
    trusted_public_key: Any | None,
    *,
    allow_untrusted: bool = False,
) -> None:
    attestation = _load_attestation(release, source)
    signature_path = release / _safe_relative(source.get("gate_signature"), suffix=".sig")
    if trusted_public_key is None:
        if allow_untrusted:
            return
        raise ReleaseContractError("an external trusted CI attestation key is required")
    try:
        signature_bytes = read_stable_file_bytes(signature_path, maximum=1024)
        signature = base64.b64decode(signature_bytes.strip(), validate=True)
        if signature_bytes != base64.b64encode(signature) + b"\n":
            raise ValueError("noncanonical gate signature")
        payload = json.dumps(attestation, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        trusted_public_key.verify(signature, payload)
    except (OSError, ValueError) as exc:
        raise ReleaseContractError("software gate attestation signature is invalid") from exc
    except Exception as exc:
        if exc.__class__.__name__ == "InvalidSignature":
            raise ReleaseContractError("software gate attestation signature does not verify") from exc
        raise


def validate_release_contract(
    release: Path,
    contract: dict[str, Any],
    images: set[str],
    files: dict[str, str],
    *,
    version: str,
    profiles: list[str],
    image_archives: dict[str, str],
    engine_image: str,
    trusted_attestation_key: Path | None = None,
    trusted_attestation_key_object: Any | None = None,
    allow_untrusted_attestation: bool = False,
) -> dict[str, Any]:
    required = {
        "source", "sboms", "third_party_notices", "model_inventory",
        "license_approvals", "persistent_state",
    }
    if set(contract) != required:
        raise ReleaseContractError("release contract fields are incomplete or unexpected")
    if len(images) != 4:
        raise ReleaseContractError("production Appliance contract requires exactly four Compose images")
    if set(image_archives) != images or len(set(image_archives.values())) != len(images):
        raise ReleaseContractError("every Compose image requires one distinct offline archive")
    source = contract.get("source")
    source_fields = {
        "commit", "gate_attestation", "gate_signature", "bundle_attestation",
        "bundle_signature", "image_revisions",
    }
    if (
        not isinstance(source, dict)
        or set(source) != source_fields
        or not isinstance(source.get("commit"), str)
        or not COMMIT.fullmatch(source["commit"])
    ):
        raise ReleaseContractError("release source commit must be an exact 40-character Git commit")
    expected_source_paths = {
        "gate_attestation": "evidence/release-attestation.json",
        "gate_signature": "evidence/release-attestation.json.sig",
        "bundle_attestation": BUNDLE_ATTESTATION_PATH,
        "bundle_signature": BUNDLE_SIGNATURE_PATH,
    }
    if any(source.get(field) != expected for field, expected in expected_source_paths.items()):
        raise ReleaseContractError("release source evidence paths must use canonical locations")
    expected_legal_paths = {
        "third_party_notices": "THIRD_PARTY_NOTICES",
        "model_inventory": "model-inventory.json",
        "license_approvals": "license-approvals.json",
    }
    if any(contract.get(field) != expected for field, expected in expected_legal_paths.items()):
        raise ReleaseContractError("release legal/model artifacts must use canonical locations")
    revisions = source.get("image_revisions")
    if not isinstance(revisions, dict) or set(revisions) != images or set(revisions.values()) != {source["commit"]}:
        raise ReleaseContractError("every Compose image revision must match the attested source commit")
    for image, archive_name in image_archives.items():
        validate_single_image_archive(
            release / archive_name,
            image,
            expected_revision=source["commit"],
        )
    role_paths = [
        CONTRACT_PATH,
        _safe_relative(source.get("gate_attestation"), suffix=".json"),
        _safe_relative(source.get("gate_signature"), suffix=".sig"),
        _safe_relative(source.get("bundle_attestation"), suffix=".json"),
        _safe_relative(source.get("bundle_signature"), suffix=".sig"),
        _safe_relative(contract.get("third_party_notices")),
        _safe_relative(contract.get("model_inventory"), suffix=".json"),
        _safe_relative(contract.get("license_approvals"), suffix=".json"),
    ]
    referenced = set(role_paths)
    sboms = contract.get("sboms")
    if not isinstance(sboms, dict) or set(sboms) != images:
        raise ReleaseContractError("every Compose image requires exactly one SBOM mapping")
    if len(set(sboms.values())) != len(images):
        raise ReleaseContractError("every Compose image requires a distinct SBOM artifact")
    sbom_paths: list[str] = []
    for image, relative in sboms.items():
        path = _safe_relative(relative, suffix=".spdx.json")
        if not path.startswith("sbom/"):
            raise ReleaseContractError("SBOM artifacts must use the canonical sbom directory")
        sbom_paths.append(path)
        referenced.add(path)
        _validate_spdx(release, path, image)
    archive_paths = [_safe_relative(path, suffix=".tar") for path in image_archives.values()]
    all_role_paths = [*role_paths, *sbom_paths, *archive_paths]
    if len(set(all_role_paths)) != len(all_role_paths):
        raise ReleaseContractError("release artifact roles must use distinct payload paths")
    reserved_runtime_paths = {
        "compose.yaml",
        "Caddyfile",
        "mediamtx.yml",
        "bin/activation_health.py",
        "bin/transaction_guard.py",
        "systemd/ksi-cctv-compose.service",
        "release-manifest.json",
        "release-manifest.sig",
        *(path for path in files if path.startswith("defaults/config/")),
    }
    if {*role_paths, *sbom_paths} & reserved_runtime_paths:
        raise ReleaseContractError("release evidence roles must not reuse runtime payload paths")
    referenced.update(archive_paths)
    if not referenced.issubset(set(files)):
        raise ReleaseContractError("release contract references unsigned or missing payload files")
    notices = release / _safe_relative(contract["third_party_notices"])
    if not notices.read_text(encoding="utf-8").strip():
        raise ReleaseContractError("THIRD_PARTY_NOTICES must not be empty")
    approval_path = str(contract["license_approvals"])
    if approval_path not in files:
        raise ReleaseContractError("license approvals are unsigned or missing")
    if engine_image not in images:
        raise ReleaseContractError("Compose engine image identity is missing")
    models = _validate_model_inventory(
        release,
        str(contract["model_inventory"]),
        engine_image,
        str(contract["third_party_notices"]),
    )
    _validate_license_approvals(release, approval_path, source["commit"], models)
    validate_persistent_state(contract["persistent_state"])
    evidence_paths = validate_release_payload_layout(
        release,
        contract,
        set(files),
        image_archives,
    )
    attested_configs = _load_attestation(release, source)["config_digests"]
    for bundled, digest in files.items():
        if not bundled.startswith("defaults/config/") or bundled == "defaults/config/cameras.json":
            continue
        source_path = bundled.removeprefix("defaults/")
        if attested_configs.get(source_path) != digest:
            raise ReleaseContractError(
                f"release default does not match the clean gated source: {bundled}"
            )
    if evidence_paths & ({*all_role_paths} | reserved_runtime_paths):
        raise ReleaseContractError("software gate evidence paths must be role-distinct")
    trusted_key = trusted_attestation_key_object
    if trusted_key is None and trusted_attestation_key is not None:
        trusted_key = load_external_ed25519_public_key(release, trusted_attestation_key)
    verify_attestation_signature(
        release,
        source,
        trusted_key,
        allow_untrusted=allow_untrusted_attestation,
    )
    verify_bundle_attestation(
        release,
        contract,
        version,
        profiles,
        image_archives,
        files,
        trusted_key,
        allow_untrusted=allow_untrusted_attestation,
    )
    return contract
