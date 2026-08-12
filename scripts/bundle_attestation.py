#!/usr/bin/env python3
"""Create the CI-signed statement that binds a gate result to one offline bundle."""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))

from release_contracts import (  # noqa: E402
    BUNDLE_ATTESTATION_PATH,
    BUNDLE_SIGNATURE_PATH,
    ReleaseContractError,
    bundle_attestation_payload,
    canonical_json,
    load_release_contract,
    path_contains_private_key_material,
    read_stable_file_bytes,
    release_payload_snapshot,
    validate_release_payload_layout,
    validate_release_payload_safety,
    validate_single_image_archive,
    verify_release_payload_unchanged,
    write_atomic_file,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def payload_files(
    release: Path,
    private_digest: str,
    image_archives: dict[str, str],
) -> dict[str, str]:
    excluded = {"release-manifest.json", "release-manifest.sig"}
    archive_images = {archive: image for image, archive in image_archives.items()}
    result: dict[str, str] = {}
    for path in sorted(release.rglob("*")):
        if path.is_symlink():
            raise ReleaseContractError(f"release must not contain symlinks: {path.relative_to(release)}")
        relative = path.relative_to(release).as_posix()
        if path.is_dir():
            continue
        if not path.is_file():
            raise ReleaseContractError(f"release contains a special filesystem entry: {relative}")
        if relative not in excluded:
            digest = sha256(path)
            if relative in archive_images:
                validate_single_image_archive(path, archive_images[relative])
            if digest == private_digest or path_contains_private_key_material(
                path, require_archive=relative in archive_images
            ):
                raise ReleaseContractError("release payload must not contain private signing-key material")
            validate_release_payload_safety(path, relative)
            result[relative] = digest
    return result


def create(
    release: Path,
    version: str,
    profiles: list[str],
    image_archives: dict[str, str],
    private_key: Path,
) -> None:
    if not profiles or len(profiles) != len(set(profiles)):
        raise ReleaseContractError("hardware profiles must be non-empty and unique")
    if not release.is_dir() or private_key.resolve().is_relative_to(release.resolve()):
        raise ReleaseContractError("release directory is required and CI private key must be outside it")
    if private_key.resolve().is_relative_to(ROOT.resolve()):
        raise ReleaseContractError("CI private key must be outside the source checkout")
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
        key = serialization.load_pem_private_key(key_data, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("not Ed25519")
    except ReleaseContractError:
        raise
    except (ImportError, OSError, ValueError, TypeError) as exc:
        raise ReleaseContractError("cryptography with an unencrypted Ed25519 CI private key is required") from exc
    with release_payload_snapshot(release) as snapshot:
        contract = load_release_contract(snapshot)
        files = payload_files(snapshot, hashlib.sha256(key_data).hexdigest(), image_archives)
        validate_release_payload_layout(snapshot, contract, set(files), image_archives)
        statement = bundle_attestation_payload(contract, version, profiles, image_archives, files)
    verify_release_payload_unchanged(
        release,
        files,
        excluded={"release-manifest.json", "release-manifest.sig"},
    )
    payload = canonical_json(statement)
    write_atomic_file(release / BUNDLE_ATTESTATION_PATH, payload + b"\n")
    write_atomic_file(
        release / BUNDLE_SIGNATURE_PATH,
        base64.b64encode(key.sign(payload)) + b"\n",
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Bind a passed CI gate to an exact offline bundle payload")
    result.add_argument("--release-dir", required=True)
    result.add_argument("--version", required=True)
    result.add_argument("--profile", action="append", required=True)
    result.add_argument("--private-key", required=True)
    result.add_argument("--image-archive", action="append", required=True, metavar="IMAGE=images/FILE.tar")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        mappings: dict[str, str] = {}
        for value in args.image_archive:
            if "=" not in value:
                raise ReleaseContractError("--image-archive must use IMAGE=images/FILE.tar")
            image, archive = value.split("=", 1)
            if not image or not archive or image in mappings:
                raise ReleaseContractError("--image-archive contains an invalid or duplicate image")
            mappings[image] = archive
        create(
            Path(args.release_dir).resolve(),
            args.version,
            args.profile,
            mappings,
            Path(args.private_key).resolve(),
        )
    except ReleaseContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
