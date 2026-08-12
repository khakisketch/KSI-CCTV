from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "installer"))
import release_contracts as contracts

from tests.release_contract_fixture import (
    add_release_contract,
    image_layer,
    image_ref,
    write_test_image_archive,
)

SPEC = importlib.util.spec_from_file_location(
    "bundle_attestation", ROOT / "scripts" / "bundle_attestation.py"
)
assert SPEC and SPEC.loader
bundle_attestation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bundle_attestation)


def _write_image_archive(path: Path, payload: bytes, image: str) -> None:
    del payload
    write_test_image_archive(path, image)


def test_bundle_attestation_tool_binds_and_signs_exact_payload(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    images = {image_ref(name): f"images/{name}.tar" for name in ("engine", "web", "mediamtx", "caddy")}
    for image, archive in images.items():
        path = release / archive
        path.parent.mkdir(exist_ok=True)
        _write_image_archive(path, archive.encode(), image)
    public_key = add_release_contract(release, images, tmp_path)
    (release / contracts.BUNDLE_ATTESTATION_PATH).unlink()
    (release / contracts.BUNDLE_SIGNATURE_PATH).unlink()

    bundle_attestation.create(
        release,
        "v1",
        ["dgx-spark-arm64"],
        images,
        tmp_path / "ci-attestation.pem",
    )

    files = {
        path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in release.rglob("*")
        if path.is_file()
    }
    contract = contracts.load_release_contract(release)
    contracts.validate_release_contract(
        release,
        contract,
        set(images),
        files,
        version="v1",
        profiles=["dgx-spark-arm64"],
        image_archives=images,
        engine_image=image_ref("engine"),
        trusted_attestation_key=public_key,
    )


def test_bundle_attestation_rejects_ci_private_key_copied_into_payload(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    images = {image_ref(name): f"images/{name}.tar" for name in ("engine", "web", "mediamtx", "caddy")}
    for image, archive in images.items():
        path = release / archive
        path.parent.mkdir(exist_ok=True)
        _write_image_archive(path, archive.encode(), image)
    add_release_contract(release, images, tmp_path)
    shutil.copy2(tmp_path / "ci-attestation.pem", release / "leaked-ci-key.pem")

    with pytest.raises(contracts.ReleaseContractError, match="private signing-key material|forbidden site/trust"):
        bundle_attestation.create(
            release,
            "v1",
            ["dgx-spark-arm64"],
            images,
            tmp_path / "ci-attestation.pem",
        )


def test_bundle_attestation_rejects_declared_site_secret_payload(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    images = {image_ref(name): f"images/{name}.tar" for name in ("engine", "web", "mediamtx", "caddy")}
    for image, archive in images.items():
        path = release / archive
        path.parent.mkdir(exist_ok=True)
        _write_image_archive(path, archive.encode(), image)
    add_release_contract(release, images, tmp_path)
    (release / "site.env").write_text("KSI_CCTV_API_KEY=customer-secret\n", encoding="utf-8")

    with pytest.raises(contracts.ReleaseContractError, match="forbidden site/trust material"):
        bundle_attestation.create(
            release,
            "v1",
            ["dgx-spark-arm64"],
            images,
            tmp_path / "ci-attestation.pem",
        )


def test_release_payload_rejects_customer_camera_defaults(tmp_path: Path) -> None:
    cameras = tmp_path / "cameras.json"
    cameras.write_text('[{"url":"rtsp://camera.invalid/live"}]', encoding="utf-8")

    with pytest.raises(contracts.ReleaseContractError, match="customer camera records"):
        contracts.validate_release_payload_safety(cameras, "defaults/config/cameras.json")


@pytest.mark.parametrize(
    ("content", "relative", "message"),
    [
        ('{"name":"site","api_key":"customer-secret"}', "license-approvals.json", "secret assignment"),
        ("camera_url: rtsps://camera.invalid/live\n", "defaults/config/default.yaml", "RTSP endpoints"),
        ("endpoint: https://service.invalid/path?token=customer-secret\n", "defaults/config/default.yaml", "credential-bearing URL"),
        ("endpoint: https://service.invalid/path?to%6ben=customer-secret\n", "defaults/config/default.yaml", "credential-bearing URL"),
        ("endpoint: https://service.invalid/path?to%256ben=customer-secret\n", "defaults/config/default.yaml", "credential-bearing URL"),
        ("endpoint: https://service.invalid/path#token=customer-secret\n", "defaults/config/default.yaml", "credential-bearing URL"),
        ("endpoint: https://service.invalid/path#to%6ben=customer-secret\n", "defaults/config/default.yaml", "credential-bearing URL"),
        ("# api_key=customer-secret\n", "Caddyfile", "secret assignment"),
    ],
)
def test_release_payload_rejects_structured_and_url_secrets(
    tmp_path: Path,
    content: str,
    relative: str,
    message: str,
) -> None:
    payload = tmp_path / Path(relative).name
    payload.write_text(content, encoding="utf-8")

    with pytest.raises(contracts.ReleaseContractError, match=message):
        contracts.validate_release_payload_safety(payload, relative)


def test_release_payload_scans_large_and_utf16_secret_assignments(tmp_path: Path) -> None:
    large = tmp_path / "large.txt"
    large.write_text("x" * (3 * 1024 * 1024) + "\napi_key=customer-secret\n", encoding="utf-8")
    utf16 = tmp_path / "utf16.txt"
    utf16.write_text("password: customer-secret\n", encoding="utf-16")

    for payload in (large, utf16):
        with pytest.raises(contracts.ReleaseContractError, match="secret assignment"):
            contracts.validate_release_payload_safety(payload, payload.name)


def test_repository_default_configs_are_release_payload_safe(tmp_path: Path) -> None:
    config = ROOT / "config"
    for source in sorted(config.rglob("*")):
        if not source.is_file() or source.name == "cameras.json" or "__pycache__" in source.parts:
            continue
        relative = f"defaults/config/{source.relative_to(config).as_posix()}"
        contracts.validate_release_payload_safety(source, relative)

    empty_cameras = tmp_path / "cameras.json"
    empty_cameras.write_text("[]\n", encoding="utf-8")
    contracts.validate_release_payload_safety(empty_cameras, "defaults/config/cameras.json")


def test_bundle_attestation_rejects_ci_private_key_reserialized_as_der(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization

    release = tmp_path / "release"
    release.mkdir()
    images = {image_ref(name): f"images/{name}.tar" for name in ("engine", "web", "mediamtx", "caddy")}
    for image, archive in images.items():
        path = release / archive
        path.parent.mkdir(exist_ok=True)
        _write_image_archive(path, archive.encode(), image)
    add_release_contract(release, images, tmp_path)
    private_key = tmp_path / "ci-attestation.pem"
    key = serialization.load_pem_private_key(private_key.read_bytes(), password=None)
    (release / "leaked-ci-key.der").write_bytes(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    with pytest.raises(contracts.ReleaseContractError, match="private signing-key material"):
        bundle_attestation.create(
            release,
            "v1",
            ["dgx-spark-arm64"],
            images,
            private_key,
        )


def test_bundle_attestation_scans_large_payloads_for_private_key_markers(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir()
    images = {image_ref(name): f"images/{name}.tar" for name in ("engine", "web", "mediamtx", "caddy")}
    for image, archive in images.items():
        path = release / archive
        path.parent.mkdir(exist_ok=True)
        _write_image_archive(path, archive.encode(), image)
    add_release_contract(release, images, tmp_path)
    private_key = tmp_path / "ci-attestation.pem"
    (release / "large-secret.bin").write_bytes(b"x" * (1024 * 1024 + 1) + private_key.read_bytes())

    with pytest.raises(contracts.ReleaseContractError, match="private signing-key material"):
        bundle_attestation.create(
            release,
            "v1",
            ["dgx-spark-arm64"],
            images,
            private_key,
        )


def test_private_key_scan_reaches_compressed_archive_members(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w:gz") as layer:
        key_member = tarfile.TarInfo("app/private-key.der")
        key_member.size = len(private_key)
        layer.addfile(key_member, io.BytesIO(private_key))
    compressed_layer = layer_buffer.getvalue()
    archive = tmp_path / "image.tar"
    with tarfile.open(archive, "w") as tar:
        member = tarfile.TarInfo("layer.tar.gz")
        member.size = len(compressed_layer)
        tar.addfile(member, io.BytesIO(compressed_layer))

    assert contracts.path_contains_private_key_material(archive) is True


def test_private_key_scan_fails_closed_on_malformed_archive_stream(tmp_path: Path) -> None:
    malformed = tmp_path / "malformed.tar.gz"
    malformed.write_bytes(b"\x1f\x8b" + b"not-a-valid-compressed-tar")

    with pytest.raises(contracts.ReleaseContractError, match="failed closed"):
        contracts.path_contains_private_key_material(malformed)


def test_private_key_scan_accepts_valid_non_tar_gzip_members(tmp_path: Path) -> None:
    compressed = gzip.compress(b"ordinary compressed changelog\n")
    archive = tmp_path / "image.tar"
    with tarfile.open(archive, "w") as outer:
        member = tarfile.TarInfo("usr/share/doc/package/changelog.gz")
        member.size = len(compressed)
        outer.addfile(member, io.BytesIO(compressed))

    assert contracts.path_contains_private_key_material(archive, require_archive=True) is False


def test_private_key_scan_detects_der_in_non_tar_gzip_member(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    compressed = gzip.compress(private_key)
    archive = tmp_path / "image.tar"
    with tarfile.open(archive, "w") as outer:
        member = tarfile.TarInfo("opaque.gz")
        member.size = len(compressed)
        outer.addfile(member, io.BytesIO(compressed))

    assert contracts.path_contains_private_key_material(archive, require_archive=True) is True


def test_image_archive_rejects_multiple_image_records(tmp_path: Path) -> None:
    archive_path = tmp_path / "images.tar"
    _write_image_archive(archive_path, b"image", image_ref("engine"))
    with tarfile.open(archive_path, "a") as archive:
        manifest = (
            b'[{"Config":"config.json","RepoTags":[],"Layers":["payload.bin"]},'
            b'{"Config":"config.json","RepoTags":[],"Layers":["payload.bin"]}]'
        )
        replacement = tarfile.TarInfo("manifest.json")
        replacement.size = len(manifest)
        archive.addfile(replacement, io.BytesIO(manifest))

    with pytest.raises(contracts.ReleaseContractError, match="duplicate member paths|exactly one image"):
        contracts.validate_single_image_archive(archive_path)


def test_image_archive_accepts_one_tagless_arm64_oci_inventory(tmp_path: Path) -> None:
    layer_buffer = io.BytesIO()
    with tarfile.open(fileobj=layer_buffer, mode="w") as layer:
        content = b"layer content"
        member = tarfile.TarInfo("payload.bin")
        member.size = len(content)
        layer.addfile(member, io.BytesIO(content))
    layer_bytes = layer_buffer.getvalue()
    layer_id = hashlib.sha256(layer_bytes).hexdigest()
    config = (
        '{"architecture":"arm64","os":"linux","rootfs":{"diff_ids":["sha256:'
        + layer_id + '"],"type":"layers"}}'
    ).encode()
    config_id = hashlib.sha256(config).hexdigest()
    oci_manifest = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": f"sha256:{config_id}",
            "size": len(config),
        },
        "layers": [{
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": f"sha256:{layer_id}",
            "size": len(layer_bytes),
        }],
    }, sort_keys=True, separators=(",", ":")).encode()
    manifest_id = hashlib.sha256(oci_manifest).hexdigest()
    archive_path = tmp_path / "image.tar"
    members = {
        "manifest.json": json.dumps([{
            "Config": f"blobs/sha256/{config_id}",
            "RepoTags": None,
            "Layers": [f"blobs/sha256/{layer_id}"],
            "LayerSources": {
                f"sha256:{layer_id}": {
                    "digest": f"sha256:{layer_id}",
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "size": len(layer_bytes),
                }
            },
        }], sort_keys=True, separators=(",", ":")).encode(),
        "index.json": json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": f"sha256:{manifest_id}",
                "size": len(oci_manifest),
            }],
        }, sort_keys=True, separators=(",", ":")).encode(),
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        f"blobs/sha256/{config_id}": config,
        f"blobs/sha256/{manifest_id}": oci_manifest,
        f"blobs/sha256/{layer_id}": layer_bytes,
    }
    with tarfile.open(archive_path, "w") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    contracts.validate_single_image_archive(archive_path, f"sha256:{config_id}")


def test_image_archive_rejects_oci_descriptor_size_mismatch(tmp_path: Path) -> None:
    layer_bytes = image_layer("engine")
    layer_id = hashlib.sha256(layer_bytes).hexdigest()
    config = json.dumps({
        "architecture": "arm64",
        "os": "linux",
        "rootfs": {"diff_ids": [f"sha256:{layer_id}"], "type": "layers"},
    }, sort_keys=True, separators=(",", ":")).encode()
    config_id = hashlib.sha256(config).hexdigest()
    manifest = json.dumps({
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {
            "mediaType": "application/vnd.oci.image.config.v1+json",
            "digest": f"sha256:{config_id}",
            "size": len(config),
        },
        "layers": [{
            "mediaType": "application/vnd.oci.image.layer.v1.tar",
            "digest": f"sha256:{layer_id}",
            "size": len(layer_bytes) + 1,
        }],
    }, sort_keys=True, separators=(",", ":")).encode()
    manifest_id = hashlib.sha256(manifest).hexdigest()
    members = {
        "manifest.json": json.dumps([{
            "Config": f"blobs/sha256/{config_id}",
            "RepoTags": None,
            "Layers": [f"blobs/sha256/{layer_id}"],
        }], sort_keys=True, separators=(",", ":")).encode(),
        "index.json": json.dumps({
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.index.v1+json",
            "manifests": [{
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": f"sha256:{manifest_id}",
                "size": len(manifest),
                "platform": {"architecture": "arm64", "os": "linux"},
            }],
        }, sort_keys=True, separators=(",", ":")).encode(),
        "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
        f"blobs/sha256/{config_id}": config,
        f"blobs/sha256/{manifest_id}": manifest,
        f"blobs/sha256/{layer_id}": layer_bytes,
    }
    archive_path = tmp_path / "image.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    with pytest.raises(contracts.ReleaseContractError, match="descriptor size"):
        contracts.validate_single_image_archive(archive_path, f"sha256:{config_id}")

    canonical_config = f"blobs/sha256/{config_id}"
    alias_config = f"alternate/{config_id}.json"
    members[alias_config] = members.pop(canonical_config)
    members["manifest.json"] = json.dumps([{
        "Config": alias_config,
        "RepoTags": None,
        "Layers": [f"blobs/sha256/{layer_id}"],
    }], sort_keys=True, separators=(",", ":")).encode()
    with tarfile.open(archive_path, "w") as archive:
        for name, content in members.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    with pytest.raises(contracts.ReleaseContractError, match="canonical blob path"):
        contracts.validate_single_image_archive(archive_path, f"sha256:{config_id}")


def test_image_archive_uses_one_aggregate_layer_expansion_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layers: list[tuple[str, bytes]] = []
    for index in range(2):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as layer:
            payload = b"x" * 10
            member = tarfile.TarInfo(f"payload-{index}.bin")
            member.size = len(payload)
            layer.addfile(member, io.BytesIO(payload))
        layers.append((f"layer-{index}.tar", buffer.getvalue()))
    config = json.dumps({
        "architecture": "arm64",
        "os": "linux",
        "rootfs": {
            "diff_ids": [f"sha256:{hashlib.sha256(content).hexdigest()}" for _name, content in layers],
            "type": "layers",
        },
    }, sort_keys=True, separators=(",", ":")).encode()
    config_id = hashlib.sha256(config).hexdigest()
    archive_path = tmp_path / "image.tar"
    manifest = json.dumps([{
        "Config": f"{config_id}.json",
        "RepoTags": None,
        "Layers": [name for name, _content in layers],
    }], sort_keys=True, separators=(",", ":")).encode()
    with tarfile.open(archive_path, "w") as archive:
        for name, content in (("manifest.json", manifest), (f"{config_id}.json", config), *layers):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    monkeypatch.setattr(contracts, "MAX_IMAGE_EXPANDED_BYTES", 15)

    with pytest.raises(contracts.ReleaseContractError, match="expansion limit"):
        contracts.validate_single_image_archive(archive_path, f"sha256:{config_id}")


def test_image_archive_rejects_compressed_layer_declared_as_tar(tmp_path: Path) -> None:
    layer_bytes = gzip.compress(image_layer("engine"))
    layer_id = hashlib.sha256(layer_bytes).hexdigest()
    config = json.dumps({
        "architecture": "arm64",
        "os": "linux",
        "rootfs": {"diff_ids": [f"sha256:{layer_id}"], "type": "layers"},
    }, sort_keys=True, separators=(",", ":")).encode()
    config_id = hashlib.sha256(config).hexdigest()
    manifest = json.dumps([{
        "Config": f"{config_id}.json",
        "RepoTags": None,
        "Layers": ["layer.tar"],
    }], sort_keys=True, separators=(",", ":")).encode()
    archive_path = tmp_path / "image.tar"
    with tarfile.open(archive_path, "w") as archive:
        for name, content in (
            ("manifest.json", manifest),
            (f"{config_id}.json", config),
            ("layer.tar", layer_bytes),
        ):
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))

    with pytest.raises(contracts.ReleaseContractError, match="compression.*media type"):
        contracts.validate_single_image_archive(archive_path, f"sha256:{config_id}")


def test_image_archive_rejects_compressed_outer_tar(tmp_path: Path) -> None:
    archive_path = tmp_path / "image.tar"
    identity = image_ref("engine")
    write_test_image_archive(archive_path, identity)
    archive_path.write_bytes(gzip.compress(archive_path.read_bytes()))

    with pytest.raises(contracts.ReleaseContractError, match="outer archive.*uncompressed"):
        contracts.validate_single_image_archive(archive_path, identity)


def test_image_archive_rejects_config_digest_mismatch_and_special_members(tmp_path: Path) -> None:
    archive_path = tmp_path / "image.tar"
    identity = image_ref("engine")
    write_test_image_archive(archive_path, identity)
    with tarfile.open(archive_path, "r") as source:
        members = [(member, source.extractfile(member).read()) for member in source if member.isfile()]
    with tarfile.open(archive_path, "w") as target:
        for member, content in members:
            if member.name.endswith(".json") and member.name != "manifest.json":
                content += b" "
                member.size = len(content)
            target.addfile(member, io.BytesIO(content))

    with pytest.raises(contracts.ReleaseContractError, match="config content digest"):
        contracts.validate_single_image_archive(archive_path, identity)

    write_test_image_archive(archive_path, identity)
    with tarfile.open(archive_path, "a") as target:
        link = tarfile.TarInfo("unexpected-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        target.addfile(link)
    with pytest.raises(contracts.ReleaseContractError, match="special member"):
        contracts.validate_single_image_archive(archive_path, identity)


def test_image_archive_rejects_classic_layer_not_bound_by_config(tmp_path: Path) -> None:
    archive_path = tmp_path / "image.tar"
    identity = image_ref("engine")
    write_test_image_archive(archive_path, identity)
    with tarfile.open(archive_path, "r") as source:
        members = [(member, source.extractfile(member).read()) for member in source if member.isfile()]
    with tarfile.open(archive_path, "w") as target:
        for member, content in members:
            if member.name == "layer.tar":
                content = image_layer("web")
                member.size = len(content)
            target.addfile(member, io.BytesIO(content))

    with pytest.raises(contracts.ReleaseContractError, match="bind the exact layer"):
        contracts.validate_single_image_archive(archive_path, identity)


def test_image_archive_provenance_must_match_gated_revision(tmp_path: Path) -> None:
    archive_path = tmp_path / "image.tar"
    identity = image_ref("engine")
    write_test_image_archive(archive_path, identity)

    with pytest.raises(contracts.ReleaseContractError, match="provenance labels"):
        contracts.validate_single_image_archive(
            archive_path,
            identity,
            expected_revision="2" * 40,
        )


def test_release_snapshot_rejects_special_files(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir(mode=0o700)
    os.chmod(release, 0o700)
    os.mkfifo(release / "unsigned.fifo")

    with (
        pytest.raises(contracts.ReleaseContractError, match="special filesystem entry"),
        contracts.release_payload_snapshot(release),
    ):
        pass


def test_release_snapshot_rejects_group_writable_tree(tmp_path: Path) -> None:
    release = tmp_path / "release"
    release.mkdir(mode=0o700)
    os.chmod(release, 0o770)

    with (
        pytest.raises(contracts.ReleaseContractError, match="operator-owned and non-writable"),
        contracts.release_payload_snapshot(release),
    ):
        pass


def test_external_trust_read_rejects_group_writable_ancestor(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o700)
    os.chmod(unsafe, 0o770)
    trust_file = unsafe / "trust.pub"
    trust_file.write_bytes(b"public")
    os.chmod(trust_file, 0o644)

    with pytest.raises(contracts.ReleaseContractError, match="unsafe ancestor custody"):
        contracts.read_stable_file_bytes(
            trust_file,
            maximum=64,
            owner_uid=os.geteuid(),
            reject_writable_by_others=True,
            safe_ancestors=True,
        )


def test_bundle_payload_rejects_duplicate_profiles() -> None:
    with pytest.raises(contracts.ReleaseContractError, match="profiles.*unique"):
        contracts.bundle_attestation_payload({}, "v1", ["same", "same"], {}, {})


def test_private_key_scan_parses_v7_tar_and_encrypted_der(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    encrypted_der = Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(b"test-password"),
    )
    assert contracts.contains_private_key_material(encrypted_der) is True

    def v7_archive(payload: bytes) -> bytes:
        header = bytearray(512)
        header[0:15] = b"private-key.der"
        header[100:108] = b"0000600\0"
        header[108:116] = b"0000000\0"
        header[116:124] = b"0000000\0"
        header[124:136] = f"{len(payload):011o}\0".encode("ascii")
        header[136:148] = b"00000000000\0"
        header[148:156] = b"        "
        header[156:157] = b"0"
        checksum = sum(header)
        header[148:156] = f"{checksum:06o}\0 ".encode("ascii")
        padding = b"\0" * ((512 - len(payload) % 512) % 512)
        return bytes(header) + payload + padding + b"\0" * 1024

    v7_payload = v7_archive(encrypted_der)
    archive = tmp_path / "v7-image.tar"
    archive.write_bytes(v7_payload)

    assert contracts.path_contains_private_key_material(archive, require_archive=True) is True

    nested = tmp_path / "nested-image.tar"
    with tarfile.open(nested, "w") as outer:
        opaque = tarfile.TarInfo("opaque.bin")
        opaque.size = len(v7_payload)
        outer.addfile(opaque, io.BytesIO(v7_payload))
    assert contracts.path_contains_private_key_material(nested, require_archive=True) is True
