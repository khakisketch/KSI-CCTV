"""Test-only signed supply-chain contract fixtures."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import tarfile
from pathlib import Path

PERSISTENT_STATE = {
    "migration_mode": "none",
    "stores": {
        "events": {"schema": 2, "rollback_readable_versions": [2]},
        "observations": {"schema": 1, "rollback_readable_versions": [1]},
        "incidents": {"schema": 1, "rollback_readable_versions": [1]},
        "cameras": {"schema": 1, "rollback_readable_versions": [1]},
        "commissioning": {"schema": 1, "rollback_readable_versions": [1]},
        "outbox": {"schema": 1, "rollback_readable_versions": [1]},
        "config": {"schema": 1, "rollback_readable_versions": [1]},
    },
}
MODEL_BYTES = b"test-model"
MODEL_SHA256 = hashlib.sha256(MODEL_BYTES).hexdigest()
SOURCE_CONFIG_PATHS = (
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
    "config/webhooks.example.yaml",
    "config/zones.example.yaml",
)


def image_layer(service: str) -> bytes:
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
        payload = f"{service}-image".encode()
        member = tarfile.TarInfo("payload.bin")
        member.size = len(payload)
        layer.addfile(member, io.BytesIO(payload))
    return layer_buffer.getvalue()


def image_config(service: str) -> bytes:
    layer_digest = hashlib.sha256(image_layer(service)).hexdigest()
    return json.dumps(
        {
            "architecture": "arm64",
            "config": {
                "Labels": {
                    "org.opencontainers.image.revision": "1" * 40,
                    "org.opencontainers.image.source": "https://example.invalid/ksi-cctv",
                    "org.opencontainers.image.version": "test-v1",
                }
            },
            "os": "linux",
            "rootfs": {"diff_ids": [f"sha256:{layer_digest}"], "type": "layers"},
            "test_service": service,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def image_ref(service: str) -> str:
    return f"sha256:{hashlib.sha256(image_config(service)).hexdigest()}"


def write_test_image_archive(path: Path, image: str) -> None:
    services = ("engine", "web", "mediamtx", "caddy")
    service = next((name for name in services if image_ref(name) == image), None)
    if service is None:
        raise ValueError(f"unknown test image identity: {image}")
    layer_bytes = image_layer(service)
    config = image_config(service)
    config_name = f"{image.removeprefix('sha256:')}.json"
    manifest = json.dumps(
        [{"Config": config_name, "RepoTags": None, "Layers": ["layer.tar"]}],
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as archive:
        for name, content in (
            ("manifest.json", manifest),
            (config_name, config),
            ("layer.tar", layer_bytes),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def add_release_contract(
    release: Path,
    image_archives: dict[str, str],
    key_dir: Path,
    *,
    version: str = "v1",
    profiles: list[str] | None = None,
) -> Path:
    """Create a structurally valid fake contract and return its external CI public key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from release_contracts import APPROVED_SKIPS, EVIDENCE_SUMMARY_PATHS

    images = set(image_archives)
    commit = "1" * 40
    project_root = Path(__file__).parents[1]
    for source_relative in SOURCE_CONFIG_PATHS:
        destination = release / "defaults" / source_relative
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes((project_root / source_relative).read_bytes())
    cameras = release / "defaults" / "config" / "cameras.json"
    if not cameras.exists():
        cameras.write_text("[]\n", encoding="utf-8")
    evidence_dir = release / "evidence"
    evidence_files: dict[str, dict[str, str]] = {}
    evidence_payloads = {
        "pytest": {
            "schema_version": 1,
            "tests": 105,
            "passed": 100,
            "failed": 0,
            "errors": 0,
            "skipped": len(APPROVED_SKIPS),
        },
        "skip": {
            "skip_count": len(APPROVED_SKIPS),
            "approved_count": len(APPROVED_SKIPS),
            "missing": [],
            "unexpected": [],
            "reused": [],
            "records": [
                {"test": test, "reason": reason, "approved": True}
                for test, reason in APPROVED_SKIPS
            ],
        },
        "pip": {"blocked": []},
        "npm": {"maximum_allowed": "high", "blocked": []},
        "playwright": {"schema_version": 1, "passed": 10, "skipped": 0},
    }
    for kind, payload in evidence_payloads.items():
        name = EVIDENCE_SUMMARY_PATHS[kind]
        relative = f"evidence/{name}"
        path = release / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, payload)
        evidence_files[kind] = {name: hashlib.sha256(path.read_bytes()).hexdigest()}
    attestation = {
        "schema_version": 1,
        "strict": True,
        "passed": True,
        "clean_tree": True,
        "commit": commit,
        "tracked_tree_sha256": "4" * 64,
        "release_script_sha256": "5" * 64,
        "key_lock_digests": {"requirements.txt": "6" * 64},
        "config_digests": {
            relative: hashlib.sha256((project_root / relative).read_bytes()).hexdigest()
            for relative in SOURCE_CONFIG_PATHS
        },
        "command": "bash scripts/verify_release_candidate.sh --strict",
        "tool_versions": {
            "python": "Python test",
            "git": "git test",
            "node": "node test",
            "npm": "npm test",
            "docker": "docker test",
            "docker_compose": "docker compose test",
        },
        "started_at_utc": "2026-01-01T00:00:00Z",
        "ended_at_utc": "2026-01-01T00:01:00Z",
        "runner": "test-runner",
        "evidence_sha256": evidence_files,
    }
    attestation_path = evidence_dir / "release-attestation.json"
    _write_json(attestation_path, attestation)
    private = Ed25519PrivateKey.generate()
    private_path = key_dir / "ci-attestation.pem"
    private_path.write_bytes(private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    os.chmod(private_path, 0o600)
    signature_path = evidence_dir / "release-attestation.json.sig"
    signature_path.write_bytes(base64.b64encode(private.sign(json.dumps(
        attestation, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii"))) + b"\n")
    public_path = key_dir / "ci-attestation.pub"
    public_path.write_bytes(base64.b64encode(private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )))
    os.chmod(public_path, 0o644)

    sboms: dict[str, str] = {}
    for index, image in enumerate(sorted(images)):
        relative = f"sbom/image-{index}.spdx.json"
        image_digest = image.rsplit("sha256:", 1)[-1]
        _write_json(release / relative, {
            "spdxVersion": "SPDX-2.3",
            "SPDXID": "SPDXRef-DOCUMENT",
            "dataLicense": "CC0-1.0",
            "name": f"test-image-{index}",
            "documentNamespace": f"https://ksi.local/spdx/test-image-{index}",
            "creationInfo": {
                "created": "2026-01-01T00:00:00Z",
                "creators": ["Tool: ksi-test-fixture"],
            },
            "documentDescribes": ["SPDXRef-Image"],
            "packages": [{
                "SPDXID": "SPDXRef-Image",
                "name": f"test-image-{index}",
                "downloadLocation": image,
                "filesAnalyzed": False,
                "licenseConcluded": "Apache-2.0",
                "licenseDeclared": "Apache-2.0",
                "copyrightText": "Copyright test fixture",
                "checksums": [{"algorithm": "SHA256", "checksumValue": image_digest}],
            }],
        })
        sboms[image] = relative

    notices = release / "THIRD_PARTY_NOTICES"
    notices.write_text(
        "TEST FIXTURE ONLY - no production license decision.\n[test-general-model]\nApache-2.0\n",
        encoding="utf-8",
    )
    engine_image = next(iter(image_archives))
    _write_json(release / "model-inventory.json", {
        "schema_version": 1,
        "models": [{
            "id": "test-general-model",
            "image": engine_image,
            "path": "/app/models/test.onnx",
            "sha256": MODEL_SHA256,
            "source_url": "https://huggingface.co/ksi-test/model",
            "source_revision": "3" * 40,
            "code_license": "Apache-2.0",
            "weight_license": "Apache-2.0",
            "dataset_license": "CC-BY-4.0",
            "notice": "THIRD_PARTY_NOTICES#test-general-model",
            "approval_reference": "TEST-LEGAL-APPROVAL-1",
        }],
    })
    _write_json(release / "license-approvals.json", {
        "schema_version": 1,
        "source_commit": commit,
        "models": {
            "test-general-model": {
                "status": "approved",
                "approval_reference": "TEST-LEGAL-APPROVAL-1",
                "model_sha256": MODEL_SHA256,
                "code_license": "Apache-2.0",
                "weight_license": "Apache-2.0",
                "dataset_license": "CC-BY-4.0",
            }
        },
    })
    contract = {
        "source": {
            "commit": commit,
            "gate_attestation": "evidence/release-attestation.json",
            "gate_signature": "evidence/release-attestation.json.sig",
            "bundle_attestation": "evidence/bundle-attestation.json",
            "bundle_signature": "evidence/bundle-attestation.json.sig",
            "image_revisions": dict.fromkeys(sorted(images), commit),
        },
        "sboms": sboms,
        "third_party_notices": "THIRD_PARTY_NOTICES",
        "model_inventory": "model-inventory.json",
        "license_approvals": "license-approvals.json",
        "persistent_state": PERSISTENT_STATE,
    }
    _write_json(release / "release-contract.json", contract)
    for directory in (release, *(path for path in release.rglob("*") if path.is_dir())):
        os.chmod(directory, 0o700)
    refresh_bundle_attestation(release, image_archives, key_dir, version=version, profiles=profiles)
    return public_path


def refresh_bundle_attestation(
    release: Path,
    image_archives: dict[str, str],
    key_dir: Path,
    *,
    version: str,
    profiles: list[str] | None = None,
) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from release_contracts import bundle_attestation_payload, canonical_json

    contract = json.loads((release / "release-contract.json").read_text(encoding="utf-8"))
    key = serialization.load_pem_private_key(
        (key_dir / "ci-attestation.pem").read_bytes(), password=None
    )
    assert isinstance(key, Ed25519PrivateKey)
    files = {
        path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in release.rglob("*")
        if path.is_file()
        and path.relative_to(release).as_posix()
        not in {"release-manifest.json", "release-manifest.sig"}
    }
    statement = bundle_attestation_payload(
        contract,
        version,
        profiles or ["dgx-spark-arm64"],
        image_archives,
        files,
    )
    evidence_dir = release / "evidence"
    statement_path = evidence_dir / "bundle-attestation.json"
    statement_path.write_bytes(canonical_json(statement) + b"\n")
    (evidence_dir / "bundle-attestation.json.sig").write_bytes(
        base64.b64encode(key.sign(canonical_json(statement))) + b"\n"
    )
