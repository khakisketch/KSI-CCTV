#!/usr/bin/env python3
"""Create a signed, reproducible KSI-CCTV offline release manifest."""
from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ksi_cctv_appliance import (
    PROFILES,
    RELEASE_SCHEMA_VERSION,
    STATE_SCHEMA_VERSION,
    VERSION_PATTERN,
    ApplianceError,
    canonical_manifest,
    compose_images,
    compose_service_images,
    healthcheck_command,
    required_release_files,
    sha256,
    validate_compose,
)
from release_contracts import (
    CONTRACT_PATH,
    ReleaseContractError,
    load_external_ed25519_public_key,
    load_release_contract,
    path_contains_private_key_material,
    read_stable_file_bytes,
    release_payload_snapshot,
    validate_release_contract,
    validate_release_payload_safety,
    validate_single_image_archive,
    verify_release_payload_unchanged,
    write_atomic_file,
)

ROOT = Path(__file__).resolve().parents[1]


def payload_files(
    release: Path,
    private_key_digest: str,
    image_archives: dict[str, str],
) -> dict[str, str]:
    files: dict[str, str] = {}
    excluded = {"release-manifest.json", "release-manifest.sig"}
    archive_images = {archive: image for image, archive in image_archives.items()}
    for path in sorted(release.rglob("*")):
        if path.is_symlink():
            raise ApplianceError(f"release must not contain symlinks: {path.relative_to(release)}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ApplianceError(f"release contains a special filesystem entry: {path.relative_to(release)}")
        relative = path.relative_to(release).as_posix()
        if relative in excluded:
            continue
        digest = sha256(path)
        if digest == private_key_digest:
            raise ApplianceError("signing key must be outside the release output")
        try:
            if relative in archive_images:
                validate_single_image_archive(path, archive_images[relative])
            if path_contains_private_key_material(
                path, require_archive=relative in archive_images
            ):
                raise ApplianceError("release payload must not contain private signing-key material")
            validate_release_payload_safety(path, relative)
        except ReleaseContractError as exc:
            raise ApplianceError(str(exc)) from exc
        if path.stat().st_size <= 1024 * 1024:
            content = path.read_bytes()
            if b"REPLACE_" in content:
                raise ApplianceError(f"release contains a placeholder hash: {path.relative_to(release)}")
        files[relative] = digest
    if "compose.yaml" not in files:
        raise ApplianceError("release must include compose.yaml")
    return files


def build_release(
    release: Path,
    version: str,
    profiles: list[str],
    private_key: Path,
    health_command: list[str],
    image_archives: dict[str, str],
    attestation_public_key: Path,
) -> None:
    try:
        resolved_key = private_key.resolve(strict=True)
    except OSError as exc:
        raise ApplianceError("Ed25519 private key does not exist") from exc
    if not release.is_dir() or private_key.is_symlink() or any(
        resolved_key.is_relative_to(boundary.resolve()) for boundary in (release, ROOT)
    ):
        raise ApplianceError(
            "release directory is required and signing key must be outside the release and source checkout"
        )
    if not VERSION_PATTERN.fullmatch(version):
        raise ApplianceError("version must be a safe release version")
    if not profiles or len(profiles) != len(set(profiles)) or not set(profiles).issubset(PROFILES):
        raise ApplianceError("hardware profiles must be supported, non-empty, and unique")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key_data = read_stable_file_bytes(
            private_key,
            maximum=64 * 1024,
            owner_uid=os.geteuid(),
            owner_only=True,
            safe_ancestors=True,
        )
        signing_key = serialization.load_pem_private_key(key_data, password=None)
        if not isinstance(signing_key, Ed25519PrivateKey):
            raise ValueError("not Ed25519")
        trusted_key = load_external_ed25519_public_key(release, attestation_public_key)
        with release_payload_snapshot(release) as snapshot:
            _build_release_from_snapshot(
                snapshot,
                release,
                version,
                profiles,
                signing_key,
                hashlib.sha256(key_data).hexdigest(),
                health_command,
                image_archives,
                trusted_key,
            )
    except ReleaseContractError as exc:
        raise ApplianceError(str(exc)) from exc
    except (ImportError, ValueError) as exc:
        raise ApplianceError("cryptography with an unencrypted Ed25519 private key is required") from exc


def _build_release_from_snapshot(
    release: Path,
    output: Path,
    version: str,
    profiles: list[str],
    signing_key: Any,
    private_key_digest: str,
    health_command: list[str],
    image_archives: dict[str, str],
    attestation_public_key: Any,
) -> None:
    validate_compose(release)
    files = payload_files(release, private_key_digest, image_archives)
    missing_runtime = required_release_files(release) - set(files)
    if missing_runtime:
        raise ApplianceError(
            f"release is missing required runtime files: {', '.join(sorted(missing_runtime))}"
        )
    inert = [name for name in files if name.startswith(("models/", "python-wheels/"))]
    if inert:
        raise ApplianceError("models and Python wheels must be embedded in the signed engine image")
    required_images = set(compose_images(release))
    if len(required_images) != 4:
        raise ApplianceError("signed production releases require exactly four Compose images")
    if set(image_archives) != required_images:
        raise ApplianceError("--image-archive must map every Compose image exactly")
    if len(set(image_archives.values())) != len(required_images):
        raise ApplianceError("every Compose image requires a distinct offline archive")
    for image, name in image_archives.items():
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not name.startswith("images/")
            or not name.endswith(".tar")
            or name not in files
        ):
            raise ApplianceError(f"invalid image archive mapping for {image}")
    release_contract = validate_release_contract(
        release,
        load_release_contract(release),
        required_images,
        files,
        version=version,
        profiles=profiles,
        image_archives=image_archives,
        engine_image=compose_service_images(release)["engine"],
        trusted_attestation_key_object=attestation_public_key,
    )
    artifacts = {name: files[name] for name in sorted(set(image_archives.values()))}
    for relative in (
        CONTRACT_PATH,
        release_contract["third_party_notices"],
        release_contract["model_inventory"],
        release_contract["license_approvals"],
        release_contract["source"]["gate_attestation"],
        release_contract["source"]["gate_signature"],
        release_contract["source"]["bundle_attestation"],
        release_contract["source"]["bundle_signature"],
        *release_contract["sboms"].values(),
    ):
        artifacts[str(relative)] = files[str(relative)]
    manifest = {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "state_schema": STATE_SCHEMA_VERSION,
        "version": version,
        "hardware_profiles": sorted(profiles),
        "files": files,
        "artifacts": artifacts,
        "images": dict(sorted(image_archives.items())),
        **release_contract,
        "healthcheck": {"command": health_command, "timeout_seconds": 60},
    }
    healthcheck_command(manifest)
    from cryptography.hazmat.primitives import serialization

    if signing_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ) == attestation_public_key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    ):
        raise ApplianceError("release-signing and CI-attestation keys must be distinct")
    verify_release_payload_unchanged(
        output,
        files,
        excluded={"release-manifest.json", "release-manifest.sig"},
    )
    manifest_bytes = canonical_manifest(manifest)
    write_atomic_file(output / "release-manifest.json", manifest_bytes)
    write_atomic_file(
        output / "release-manifest.sig",
        base64.b64encode(signing_key.sign(manifest_bytes)),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build a signed KSI-CCTV offline release")
    result.add_argument("--release-dir", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--profile", action="append", required=True, choices=sorted(PROFILES))
    result.add_argument("--private-key", required=True, help="PEM Ed25519 private key outside --release-dir")
    result.add_argument(
        "--attestation-public-key",
        required=True,
        help="trusted external CI Ed25519 public key; never copied into the release",
    )
    result.add_argument(
        "--image-archive",
        action="append",
        required=True,
        metavar="IMAGE=images/FILE.tar",
        help="map each sha256:<config-id> Compose image to its offline Docker archive",
    )
    result.add_argument(
        "--health-command",
        nargs="+",
        required=True,
        help="must be: /usr/bin/python3 /opt/ksi-cctv/current/bin/activation_health.py",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        mappings: dict[str, str] = {}
        for value in args.image_archive:
            if "=" not in value:
                raise ApplianceError("--image-archive must use IMAGE=images/FILE.tar")
            image, archive = value.split("=", 1)
            if not image or not archive or image in mappings:
                raise ApplianceError("--image-archive contains an invalid or duplicate image")
            mappings[image] = archive
        build_release(
            Path(args.release_dir).resolve(),
            args.version,
            args.profile,
            Path(args.private_key).resolve(),
            args.health_command,
            mappings,
            Path(args.attestation_public_key).resolve(),
        )
    except ApplianceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
