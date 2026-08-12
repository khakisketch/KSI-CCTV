#!/usr/bin/env python3
"""Verify a KSI-CCTV support bundle without trusting its contents."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any


def canonical_json(data: Any) -> bytes:
    return json.dumps(data, ensure_ascii=False, default=str, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _public_key(encoded: str):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    return Ed25519PublicKey.from_public_bytes(base64.b64decode(encoded, validate=True))


def _fingerprint(encoded_key: str) -> str:
    return hashlib.sha256(base64.b64decode(encoded_key, validate=True)).hexdigest()


def verify_bundle(path: Path, expected_audit_key_fingerprint: str) -> dict[str, Any]:
    expected_fingerprint = expected_audit_key_fingerprint.lower()
    if len(expected_fingerprint) != 64 or any(char not in "0123456789abcdef" for char in expected_fingerprint):
        raise ValueError("expected audit key fingerprint must be a SHA-256 hexadecimal digest")
    with zipfile.ZipFile(path) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, json.JSONDecodeError) as exc:
            raise ValueError("bundle manifest is missing or invalid") from exc
        if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
            raise ValueError("unsupported support bundle manifest")
        signature = manifest.pop("signature", None)
        if not isinstance(signature, str) or not isinstance(manifest.get("audit_public_key"), str):
            raise ValueError("bundle has no verifiable signature")
        actual_fingerprint = _fingerprint(manifest["audit_public_key"])
        if actual_fingerprint != expected_fingerprint:
            raise ValueError("bundle audit public key does not match the expected commissioning fingerprint")
        _public_key(manifest["audit_public_key"]).verify(
            base64.b64decode(signature, validate=True), canonical_json(manifest)
        )
        files = manifest.get("files")
        if not isinstance(files, dict):
            raise ValueError("bundle manifest has no file hashes")
        for name, expected in files.items():
            if not isinstance(name, str) or not isinstance(expected, str):
                raise ValueError("bundle manifest has an invalid file hash")
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != expected:
                raise ValueError(f"bundle file hash mismatch: {name}")
        audit = json.loads(archive.read("audit.json"))
        entries = audit.get("entries") if isinstance(audit, dict) else None
        if not isinstance(entries, list):
            raise ValueError("bundle audit data is invalid")
        anchor = audit.get("anchor_previous_hash")
        if anchor is not None and not isinstance(anchor, str):
            raise ValueError("bundle audit anchor is invalid")
        previous_hash: str | None = anchor
        key = _public_key(manifest["audit_public_key"])
        for entry in reversed(entries):
            if not isinstance(entry, dict):
                raise ValueError("bundle audit entry is invalid")
            signature = entry.pop("signature", None)
            entry_hash = entry.pop("entry_hash", None)
            if not isinstance(signature, str) or not isinstance(entry_hash, str):
                raise ValueError("bundle audit entry is unsigned")
            if entry.get("previous_hash") != previous_hash:
                raise ValueError("bundle audit hash chain is broken")
            if hashlib.sha256(canonical_json(entry)).hexdigest() != entry_hash:
                raise ValueError("bundle audit entry hash mismatch")
            key.verify(base64.b64decode(signature, validate=True), bytes.fromhex(entry_hash))
            previous_hash = entry_hash
        return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify KSI-CCTV support bundle hashes and signatures")
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument(
        "--expected-audit-key-fingerprint",
        required=True,
        help="SHA-256 fingerprint recorded during Appliance commissioning",
    )
    args = parser.parse_args(argv)
    try:
        manifest = verify_bundle(args.bundle, args.expected_audit_key_fingerprint)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"error: signature verification failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    fingerprint = _fingerprint(manifest["audit_public_key"])
    print(f"OK: {args.bundle} audit-public-key-sha256={fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
