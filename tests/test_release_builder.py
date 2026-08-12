import base64
import importlib.util
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.release_contract_fixture import (
    add_release_contract,
    write_test_image_archive,
)
from tests.release_contract_fixture import (
    image_ref as fixture_image_ref,
)

INSTALLER = Path(__file__).parents[1] / "installer"
sys.path.insert(0, str(INSTALLER))
import ksi_cctv_appliance as appliance

BUILDER_SPEC = importlib.util.spec_from_file_location("release_builder", INSTALLER / "release_builder.py")
builder = importlib.util.module_from_spec(BUILDER_SPEC)
assert BUILDER_SPEC.loader is not None
BUILDER_SPEC.loader.exec_module(builder)


class ReleaseBuilderTests(unittest.TestCase):
    @staticmethod
    def write_image_archive(path: Path, payload: bytes, image: str) -> None:
        del payload
        write_test_image_archive(path, image)

    @staticmethod
    def image_refs(image_ref: str) -> dict[str, str]:
        return {
            "engine": image_ref,
            "web": fixture_image_ref("web"),
            "mediamtx": fixture_image_ref("mediamtx"),
            "https": fixture_image_ref("caddy"),
        }

    @classmethod
    def appliance_compose(cls, image_ref: str) -> str:
        refs = cls.image_refs(image_ref)
        template = (INSTALLER / "compose.appliance.yaml.template").read_text(encoding="utf-8")
        replacements = {
            "sha256:REPLACE_ENGINE_ARM64_DIGEST": refs["engine"],
            "sha256:REPLACE_WEB_ARM64_DIGEST": refs["web"],
            "sha256:REPLACE_MEDIAMTX_ARM64_DIGEST": refs["mediamtx"],
            "sha256:REPLACE_CADDY_ARM64_DIGEST": refs["https"],
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        return template

    @classmethod
    def add_images(cls, release: Path, image_ref: str) -> dict[str, str]:
        image_dir = release / "images"
        image_dir.mkdir(exist_ok=True)
        result: dict[str, str] = {}
        for service, ref in cls.image_refs(image_ref).items():
            archive_name = "caddy" if service == "https" else service
            relative = f"images/{archive_name}.tar"
            payload = f"{service}-image".encode()
            cls.write_image_archive(release / relative, payload, ref)
            result[ref] = relative
        return result

    def add_runtime_files(self, release: Path) -> None:
        bin_dir = release / "bin"
        bin_dir.mkdir(exist_ok=True)
        shutil.copy2(INSTALLER / "activation_health.py", bin_dir / "activation_health.py")
        shutil.copy2(INSTALLER / "transaction_guard.py", bin_dir / "transaction_guard.py")
        systemd_dir = release / "systemd"
        systemd_dir.mkdir(exist_ok=True)
        shutil.copy2(INSTALLER / "systemd" / appliance.SERVICE, systemd_dir / appliance.SERVICE)
        shutil.copy2(INSTALLER / "Caddyfile", release / "Caddyfile")
        shutil.copy2(INSTALLER.parent / "config" / "mediamtx.yml", release / "mediamtx.yml")

    def private_key(self, directory: Path) -> Path:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        path = directory / "signing.pem"
        path.write_bytes(
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        )
        os.chmod(path, 0o600)
        return path

    def test_builder_signs_all_payloads_and_verifier_accepts_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            image_ref = fixture_image_ref("engine")
            (release / "compose.yaml").write_text(
                self.appliance_compose(image_ref), encoding="utf-8"
            )
            images = self.add_images(release, image_ref)
            self.add_runtime_files(release)
            key_path = self.private_key(root)
            attestation_key = add_release_contract(release, images, root)
            builder.build_release(
                release,
                "v1",
                ["dgx-spark-arm64"],
                key_path,
                list(appliance.ACTIVATION_HEALTH_COMMAND),
                images,
                attestation_key,
            )
            manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
            self.assertIn("images/engine.tar", manifest["files"])
            self.assertIn("images/engine.tar", manifest["artifacts"])
            from cryptography.hazmat.primitives import serialization

            private = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            public = root / "signing.pub"
            public.write_bytes(
                base64.b64encode(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
            )
            os.chmod(public, 0o644)
            appliance.verify_release(release, "dgx-spark-arm64", public, False, attestation_key)

    def test_builder_rejects_reuse_of_ci_key_for_release_signing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            image_ref = fixture_image_ref("engine")
            (release / "compose.yaml").write_text(self.appliance_compose(image_ref), encoding="utf-8")
            images = self.add_images(release, image_ref)
            self.add_runtime_files(release)
            attestation_key = add_release_contract(release, images, root)

            with self.assertRaisesRegex(appliance.ApplianceError, "must be distinct"):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    root / "ci-attestation.pem",
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    images,
                    attestation_key,
                )

    def test_builder_rejects_payload_mutation_during_signing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            image_ref = fixture_image_ref("engine")
            (release / "compose.yaml").write_text(self.appliance_compose(image_ref), encoding="utf-8")
            images = self.add_images(release, image_ref)
            self.add_runtime_files(release)
            key_path = self.private_key(root)
            attestation_key = add_release_contract(release, images, root)
            validate_contract = builder.validate_release_contract

            def mutate_source(*args, **kwargs):
                result = validate_contract(*args, **kwargs)
                (release / "Caddyfile").write_text("changed during signing\n", encoding="utf-8")
                return result

            with (
                patch.object(builder, "validate_release_contract", side_effect=mutate_source),
                self.assertRaisesRegex(appliance.ApplianceError, "changed during signing"),
            ):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    images,
                    attestation_key,
                )
            self.assertFalse((release / "release-manifest.json").exists())

    def test_builder_rejects_release_private_key_inside_source_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            key_path = self.private_key(root)

            with (
                patch.object(builder, "ROOT", root),
                self.assertRaisesRegex(appliance.ApplianceError, "source checkout"),
            ):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    {},
                    root / "unused.pub",
                )

    def test_builder_rejects_duplicate_hardware_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            key_path = self.private_key(root)

            with self.assertRaisesRegex(appliance.ApplianceError, "profiles.*unique"):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64", "dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    {},
                    root / "unused.pub",
                )

    def test_builder_rejects_placeholder_compose_and_embedded_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            (release / "compose.yaml").write_text("services:\n  web:\n    image: sha256:REPLACE_DIGEST\n", encoding="utf-8")
            key_path = self.private_key(root)
            with self.assertRaises(appliance.ApplianceError):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    {fixture_image_ref("web"): "images/web.tar"},
                    root / "unused-attestation.pub",
                )
            (release / "compose.yaml").write_text(
                self.appliance_compose(fixture_image_ref("engine")),
                encoding="utf-8",
            )
            image_archives = self.add_images(release, fixture_image_ref("engine"))
            self.add_runtime_files(release)
            attestation_key = add_release_contract(release, image_archives, root)
            (release / "copied-key.pem").write_bytes(key_path.read_bytes())
            with self.assertRaises(appliance.ApplianceError):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    image_archives,
                    attestation_key,
                )

    def test_builder_rejects_private_key_reserialized_as_der(self):
        from cryptography.hazmat.primitives import serialization

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            image_ref = fixture_image_ref("engine")
            (release / "compose.yaml").write_text(self.appliance_compose(image_ref), encoding="utf-8")
            image_archives = self.add_images(release, image_ref)
            self.add_runtime_files(release)
            key_path = self.private_key(root)
            attestation_key = add_release_contract(release, image_archives, root)
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            (release / "copied-key.der").write_bytes(
                key.private_bytes(
                    serialization.Encoding.DER,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )

            with self.assertRaisesRegex(appliance.ApplianceError, "private signing-key material"):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    image_archives,
                    attestation_key,
                )

    def test_appliance_template_has_separate_internal_services_and_stable_mounts(self):
        template = (INSTALLER / "compose.appliance.yaml.template").read_text(encoding="utf-8")

        self.assertIn("  engine:", template)
        self.assertIn("  web:", template)
        self.assertIn("  mediamtx:", template)
        self.assertIn("/var/lib/ksi-cctv:/app/data", template)
        self.assertIn("KSI_CCTV_GATEWAY_RTSP_URL: rtsp://mediamtx:8554", template)
        self.assertNotIn("8554:8554", template)
        self.assertNotIn('"8888:8888"', template)
        self.assertEqual(template.count("sha256:REPLACE_"), 4)

    def test_appliance_template_bounds_all_container_logs(self):
        template = (INSTALLER / "compose.appliance.yaml.template").read_text(encoding="utf-8")

        self.assertEqual(template.count("driver: json-file"), 4)
        self.assertEqual(template.count('max-size: "10m"'), 4)
        self.assertEqual(template.count('max-file: "5"'), 4)
        self.assertEqual(template.count("pull_policy: never"), 4)
        self.assertEqual(template.count('restart: "no"'), 4)
        self.assertNotIn("restart: unless-stopped", template)

    def test_appliance_proxy_enforces_browser_security_headers(self):
        caddyfile = (INSTALLER / "Caddyfile").read_text(encoding="utf-8")

        self.assertIn('Strict-Transport-Security "max-age=31536000"', caddyfile)
        self.assertIn('X-Content-Type-Options "nosniff"', caddyfile)
        self.assertIn('X-Frame-Options "DENY"', caddyfile)
        self.assertIn('Referrer-Policy "no-referrer"', caddyfile)
        self.assertIn("frame-ancestors 'none'", caddyfile)
        self.assertIn('Permissions-Policy "camera=(), microphone=()', caddyfile)
        self.assertIn("\t\t-Server", caddyfile)

    def test_builder_accepts_complete_four_image_appliance_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            template = (INSTALLER / "compose.appliance.yaml.template").read_text(encoding="utf-8")
            replacements = {
                "REPLACE_ENGINE_ARM64_DIGEST": fixture_image_ref("engine").removeprefix("sha256:"),
                "REPLACE_WEB_ARM64_DIGEST": fixture_image_ref("web").removeprefix("sha256:"),
                "REPLACE_MEDIAMTX_ARM64_DIGEST": fixture_image_ref("mediamtx").removeprefix("sha256:"),
                "REPLACE_CADDY_ARM64_DIGEST": fixture_image_ref("caddy").removeprefix("sha256:"),
            }
            for marker, digest in replacements.items():
                template = template.replace(marker, digest)
            (release / "compose.yaml").write_text(template, encoding="utf-8")
            shutil.copy2(INSTALLER / "Caddyfile", release / "Caddyfile")
            shutil.copy2(INSTALLER.parent / "config" / "mediamtx.yml", release / "mediamtx.yml")
            bin_dir = release / "bin"
            bin_dir.mkdir()
            shutil.copy2(INSTALLER / "activation_health.py", bin_dir / "activation_health.py")
            shutil.copy2(INSTALLER / "transaction_guard.py", bin_dir / "transaction_guard.py")
            systemd_dir = release / "systemd"
            systemd_dir.mkdir()
            shutil.copy2(INSTALLER / "systemd" / appliance.SERVICE, systemd_dir / appliance.SERVICE)
            image_dir = release / "images"
            image_dir.mkdir()
            image_archives = {}
            for index, image in enumerate(appliance.compose_images(release)):
                relative = f"images/image-{index}.tar"
                payload = f"image-{index}".encode()
                self.write_image_archive(release / relative, payload, image)
                image_archives[image] = relative
            key_path = self.private_key(root)
            attestation_key = add_release_contract(release, image_archives, root)

            builder.build_release(
                release,
                "v1",
                ["dgx-spark-arm64"],
                key_path,
                list(appliance.ACTIVATION_HEALTH_COMMAND),
                image_archives,
                attestation_key,
            )

            manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(len(manifest["images"]), 4)
            self.assertEqual(set(manifest["images"]), set(appliance.compose_images(release)))

    def test_builder_rejects_missing_boot_health_script(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            image_ref = fixture_image_ref("engine")
            (release / "compose.yaml").write_text(
                self.appliance_compose(image_ref),
                encoding="utf-8",
            )
            image_archives = self.add_images(release, image_ref)
            systemd_dir = release / "systemd"
            systemd_dir.mkdir()
            (systemd_dir / appliance.SERVICE).write_text("[Unit]\nDescription=test\n", encoding="utf-8")
            key_path = self.private_key(root)
            attestation_key = add_release_contract(release, image_archives, root)

            with self.assertRaises(appliance.ApplianceError):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    image_archives,
                    attestation_key,
                )

    def test_builder_rejects_incomplete_appliance_topology(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release"
            release.mkdir()
            image_ref = fixture_image_ref("web")
            (release / "compose.yaml").write_text(
                f"services:\n  web:\n    image: {image_ref}\n"
                "    container_name: ksi-cctv-web\n"
                "    healthcheck:\n      test: [\"CMD\", \"true\"]\n",
                encoding="utf-8",
            )
            image_dir = release / "images"
            image_dir.mkdir()
            with tarfile.open(image_dir / "web.tar", "w") as archive:
                member = tarfile.TarInfo("payload.bin")
                member.size = len(b"image")
                archive.addfile(member, io.BytesIO(b"image"))
            self.add_runtime_files(release)
            key_path = self.private_key(root)

            with self.assertRaises(appliance.ApplianceError):
                builder.build_release(
                    release,
                    "v1",
                    ["dgx-spark-arm64"],
                    key_path,
                    list(appliance.ACTIVATION_HEALTH_COMMAND),
                    {image_ref: "images/web.tar"},
                    root / "unused-attestation.pub",
                )
