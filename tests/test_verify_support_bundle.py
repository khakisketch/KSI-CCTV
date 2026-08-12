from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import sys
import tempfile
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MODULE = Path(__file__).parents[1] / "scripts" / "verify_support_bundle.py"
SPEC = importlib.util.spec_from_file_location("verify_support_bundle", MODULE)
assert SPEC and SPEC.loader
verify_support_bundle = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify_support_bundle
SPEC.loader.exec_module(verify_support_bundle)


def test_verifier_accepts_signed_bundle_and_rejects_tampered_file() -> None:
    key = Ed25519PrivateKey.generate()
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode("ascii")
    entry = {
        "timestamp": "2026-07-26T00:00:00Z",
        "event": "test",
        "previous_hash": None,
    }
    entry_hash = hashlib.sha256(verify_support_bundle.canonical_json(entry)).hexdigest()
    entry["entry_hash"] = entry_hash
    entry["signature"] = base64.b64encode(key.sign(bytes.fromhex(entry_hash))).decode("ascii")
    retained = {
        "timestamp": "2026-07-26T00:01:00Z",
        "event": "retained",
        "previous_hash": entry_hash,
    }
    retained_hash = hashlib.sha256(verify_support_bundle.canonical_json(retained)).hexdigest()
    retained["entry_hash"] = retained_hash
    retained["signature"] = base64.b64encode(key.sign(bytes.fromhex(retained_hash))).decode("ascii")
    files = {
        "commissioning.json": b"{}",
        "health.json": b"{}",
        "audit.json": json.dumps(
            {"entries": [retained], "anchor_previous_hash": entry_hash}, separators=(",", ":")
        ).encode("utf-8"),
    }
    manifest = {
        "schema_version": 2,
        "product": "KSI-CCTV",
        "generated_at": "2026-07-26T00:00:00Z",
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
        "audit_public_key": public,
        "redacted": True,
    }
    manifest["signature"] = base64.b64encode(key.sign(verify_support_bundle.canonical_json(manifest))).decode("ascii")

    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / "support.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name, value in files.items():
                archive.writestr(name, value)
        fingerprint = verify_support_bundle._fingerprint(public)
        verify_support_bundle.verify_bundle(bundle, fingerprint)

        tampered = Path(temp) / "tampered.zip"
        with zipfile.ZipFile(tampered, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name, value in files.items():
                archive.writestr(name, b'{"tampered":true}' if name == "health.json" else value)
        try:
            verify_support_bundle.verify_bundle(tampered, fingerprint)
        except ValueError as exc:
            assert "hash mismatch" in str(exc)
        else:
            raise AssertionError("tampered bundle was accepted")


def test_verifier_rejects_bundle_with_an_untrusted_key() -> None:
    trusted_key = Ed25519PrivateKey.generate()
    expected_fingerprint = hashlib.sha256(
        trusted_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).hexdigest()
    forged_key = Ed25519PrivateKey.generate()
    forged_public = base64.b64encode(
        forged_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode("ascii")
    files = {"audit.json": b'{"entries":[]}', "health.json": b"{}"}
    manifest = {
        "schema_version": 2,
        "files": {name: hashlib.sha256(value).hexdigest() for name, value in files.items()},
        "audit_public_key": forged_public,
    }
    manifest["signature"] = base64.b64encode(forged_key.sign(verify_support_bundle.canonical_json(manifest))).decode(
        "ascii"
    )

    with tempfile.TemporaryDirectory() as temp:
        bundle = Path(temp) / "forged-support.zip"
        with zipfile.ZipFile(bundle, "w") as archive:
            archive.writestr("manifest.json", json.dumps(manifest))
            for name, value in files.items():
                archive.writestr(name, value)
        try:
            verify_support_bundle.verify_bundle(bundle, expected_fingerprint)
        except ValueError as exc:
            assert "does not match" in str(exc)
        else:
            raise AssertionError("bundle signed by an untrusted key was accepted")
