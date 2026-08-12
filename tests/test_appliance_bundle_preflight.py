from __future__ import annotations

import base64
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.release_contract_fixture import add_release_contract, image_ref, write_test_image_archive

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "installer"))
BUILDER_SPEC = importlib.util.spec_from_file_location(
    "release_builder", ROOT / "installer" / "release_builder.py"
)
assert BUILDER_SPEC and BUILDER_SPEC.loader
release_builder = importlib.util.module_from_spec(BUILDER_SPEC)
BUILDER_SPEC.loader.exec_module(release_builder)


def _write_image_archive(path: Path, image: str) -> None:
    write_test_image_archive(path, image)


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required for bundle render")
def test_signed_bundle_preflight_renders_compose_without_loading_images() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        release = root / "release"
        (release / "images").mkdir(parents=True)
        (release / "bin").mkdir()
        compose = (ROOT / "installer" / "compose.appliance.yaml.template").read_text(encoding="utf-8")
        images = {
            image_ref("engine"): "images/engine.tar",
            image_ref("web"): "images/web.tar",
            image_ref("mediamtx"): "images/mediamtx.tar",
            image_ref("caddy"): "images/caddy.tar",
        }
        replacements = {
            "REPLACE_ENGINE_ARM64_DIGEST": image_ref("engine").removeprefix("sha256:"),
            "REPLACE_WEB_ARM64_DIGEST": image_ref("web").removeprefix("sha256:"),
            "REPLACE_MEDIAMTX_ARM64_DIGEST": image_ref("mediamtx").removeprefix("sha256:"),
            "REPLACE_CADDY_ARM64_DIGEST": image_ref("caddy").removeprefix("sha256:"),
        }
        for placeholder, digest in replacements.items():
            compose = compose.replace(placeholder, digest)
        (release / "compose.yaml").write_text(compose, encoding="utf-8")
        shutil.copy2(ROOT / "installer" / "Caddyfile", release / "Caddyfile")
        shutil.copy2(ROOT / "config" / "mediamtx.yml", release / "mediamtx.yml")
        shutil.copy2(ROOT / "installer" / "activation_health.py", release / "bin" / "activation_health.py")
        shutil.copy2(ROOT / "installer" / "transaction_guard.py", release / "bin" / "transaction_guard.py")
        (release / "systemd").mkdir()
        shutil.copy2(
            ROOT / "installer" / "systemd" / "ksi-cctv-compose.service",
            release / "systemd" / "ksi-cctv-compose.service",
        )
        for image, archive in images.items():
            _write_image_archive(release / archive, image)

        private_key = Ed25519PrivateKey.generate()
        private_path = root / "release-signing.pem"
        private_path.write_bytes(
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        os.chmod(private_path, 0o600)
        public_path = root / "release-signing.pub"
        public_path.write_bytes(
            base64.b64encode(
                private_key.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                )
            )
        )
        os.chmod(public_path, 0o644)
        attestation_public_path = add_release_contract(release, images, root, version="fixture-1")
        release_builder.build_release(
            release,
            "fixture-1",
            ["dgx-spark-arm64"],
            private_path,
            ["/usr/bin/python3", "/opt/ksi-cctv/current/bin/activation_health.py"],
            images,
            attestation_public_path,
        )

        result = subprocess.run(
            [
                "bash",
                str(ROOT / "scripts" / "verify_appliance_bundle.sh"),
                "--release-dir",
                str(release),
                "--profile",
                "dgx-spark-arm64",
                "--public-key",
                str(public_path),
                "--attestation-public-key",
                str(attestation_public_path),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )

    assert result.returncode == 0, result.stderr
    assert "PASS: signed bundle preflight completed" in result.stdout


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required for bundle render")
def test_preflight_rejects_an_invalid_unit_shipped_in_the_signed_bundle() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        release = root / "release"
        (release / "images").mkdir(parents=True)
        (release / "bin").mkdir()
        compose = (ROOT / "installer" / "compose.appliance.yaml.template").read_text(encoding="utf-8")
        images = {
            image_ref("engine"): "images/engine.tar",
            image_ref("web"): "images/web.tar",
            image_ref("mediamtx"): "images/mediamtx.tar",
            image_ref("caddy"): "images/caddy.tar",
        }
        for placeholder, digest in {
                "REPLACE_ENGINE_ARM64_DIGEST": image_ref("engine").removeprefix("sha256:"),
                "REPLACE_WEB_ARM64_DIGEST": image_ref("web").removeprefix("sha256:"),
                "REPLACE_MEDIAMTX_ARM64_DIGEST": image_ref("mediamtx").removeprefix("sha256:"),
                "REPLACE_CADDY_ARM64_DIGEST": image_ref("caddy").removeprefix("sha256:"),
        }.items():
            compose = compose.replace(placeholder, digest)
        (release / "compose.yaml").write_text(compose, encoding="utf-8")
        shutil.copy2(ROOT / "installer" / "Caddyfile", release / "Caddyfile")
        shutil.copy2(ROOT / "config" / "mediamtx.yml", release / "mediamtx.yml")
        shutil.copy2(ROOT / "installer" / "activation_health.py", release / "bin" / "activation_health.py")
        shutil.copy2(ROOT / "installer" / "transaction_guard.py", release / "bin" / "transaction_guard.py")
        (release / "systemd").mkdir()
        (release / "systemd" / "ksi-cctv-compose.service").write_text("[Unit]\nDescription=invalid\n", encoding="utf-8")
        for image, archive in images.items():
            _write_image_archive(release / archive, image)

        private_key = Ed25519PrivateKey.generate()
        private_path = root / "release-signing.pem"
        private_path.write_bytes(private_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ))
        os.chmod(private_path, 0o600)
        public_path = root / "release-signing.pub"
        public_path.write_bytes(base64.b64encode(private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )))
        os.chmod(public_path, 0o644)
        attestation_public_path = add_release_contract(
            release, images, root, version="fixture-invalid-unit"
        )
        release_builder.build_release(
            release, "fixture-invalid-unit", ["dgx-spark-arm64"], private_path,
            ["/usr/bin/python3", "/opt/ksi-cctv/current/bin/activation_health.py"], images,
            attestation_public_path,
        )

        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / "verify_appliance_bundle.sh"), "--release-dir", str(release),
             "--profile", "dgx-spark-arm64", "--public-key", str(public_path),
             "--attestation-public-key", str(attestation_public_path)],
            cwd=ROOT, check=False, capture_output=True, text=True, timeout=60,
        )

    assert result.returncode == 2
    assert "signed systemd unit violates" in result.stderr
