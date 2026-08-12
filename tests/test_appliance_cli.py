import base64
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.release_contract_fixture import (
    add_release_contract,
    refresh_bundle_attestation,
    write_test_image_archive,
)
from tests.release_contract_fixture import (
    image_ref as fixture_image_ref,
)

API_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
WEB_SECRET = base64.urlsafe_b64encode(bytes(range(32, 64))).decode("ascii")
CAPTURE_KEY = base64.urlsafe_b64encode(bytes(range(64, 96))).decode("ascii")
WEB_PASSWORD = "Web-Pass_2026!Secure"

MODULE = Path(__file__).parents[1] / "installer" / "ksi_cctv_appliance.py"
SPEC = importlib.util.spec_from_file_location("appliance", MODULE)
appliance = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(appliance)


class ApplianceCliTests(unittest.TestCase):
    @staticmethod
    def appliance_compose(image_ref: str) -> str:
        refs = {
            "engine": image_ref,
            "web": fixture_image_ref("web"),
            "mediamtx": fixture_image_ref("mediamtx"),
            "https": fixture_image_ref("caddy"),
        }
        template = (MODULE.parent / "compose.appliance.yaml.template").read_text(encoding="utf-8")
        replacements = {
            "sha256:REPLACE_ENGINE_ARM64_DIGEST": refs["engine"],
            "sha256:REPLACE_WEB_ARM64_DIGEST": refs["web"],
            "sha256:REPLACE_MEDIAMTX_ARM64_DIGEST": refs["mediamtx"],
            "sha256:REPLACE_CADDY_ARM64_DIGEST": refs["https"],
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        return template

    def make_release(self, directory: Path, version: str = "v1", signed: bool = False) -> tuple[Path, Path | None]:
        site_env = directory / "site.env"
        site_env.write_text(
            f"KSI_CCTV_API_KEY={API_KEY}\n"
            "KSI_CCTV_SITE_ADDRESS=cctv.test\n"
            f"KSI_CCTV_WEB_PASSWORD={WEB_PASSWORD}\n"
            f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n"
            f"KSI_CCTV_CAPTURE_KEY={CAPTURE_KEY}\n",
            encoding="utf-8",
        )
        os.chmod(site_env, 0o600)
        release = directory / version
        release.mkdir()
        image_ref = fixture_image_ref("engine")
        payload = release / "compose.yaml"
        payload.write_text(self.appliance_compose(image_ref), encoding="utf-8")
        image_dir = release / "images"
        image_dir.mkdir()
        images: dict[str, str] = {}
        for service in ("engine", "web", "mediamtx", "caddy"):
            image = image_dir / f"{service}.tar"
            identity = fixture_image_ref(service)
            write_test_image_archive(image, identity)
            images[identity] = f"images/{service}.tar"
        health_script = release / "bin" / "activation_health.py"
        health_script.parent.mkdir()
        health_script.write_bytes((MODULE.parent / "activation_health.py").read_bytes())
        (health_script.parent / "transaction_guard.py").write_bytes(
            (MODULE.parent / "transaction_guard.py").read_bytes()
        )
        unit = release / "systemd" / appliance.SERVICE
        unit.parent.mkdir()
        unit.write_bytes((MODULE.parent / "systemd" / appliance.SERVICE).read_bytes())
        (release / "Caddyfile").write_bytes((MODULE.parent / "Caddyfile").read_bytes())
        (release / "mediamtx.yml").write_bytes(
            (MODULE.parents[1] / "config" / "mediamtx.yml").read_bytes()
        )
        add_release_contract(release, images, directory, version=version)
        files = {
            path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in release.rglob("*")
            if path.is_file()
        }
        contract = json.loads((release / "release-contract.json").read_text(encoding="utf-8"))
        manifest = {
            "schema_version": appliance.RELEASE_SCHEMA_VERSION,
            "state_schema": appliance.STATE_SCHEMA_VERSION,
            "version": version,
            "hardware_profiles": ["dgx-spark-arm64"],
            "files": files,
            "artifacts": {name: files[name] for name in images.values()},
            "images": images,
            **contract,
            "healthcheck": {"command": list(appliance.ACTIVATION_HEALTH_COMMAND), "timeout_seconds": 5},
        }
        (release / "release-manifest.json").write_bytes(appliance.canonical_manifest(manifest))
        public_key = None
        if signed:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            private_key = Ed25519PrivateKey.generate()
            public_key = directory / f"{version}.pub"
            public_key.write_bytes(base64.b64encode(private_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )))
            os.chmod(public_key, 0o644)
            (release / "release-manifest.sig").write_bytes(base64.b64encode(private_key.sign(appliance.canonical_manifest(manifest))))
        return release, public_key

    def successful_command(self, command, timeout=None):
        stdout = ""
        if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
            stdout = "/\n"
        elif command[:4] == ["docker", "image", "inspect", "--format"]:
            stdout = f"{command[-1]} arm64\n"
        elif command[:4] == ["docker", "image", "load", "--input"]:
            stdout = "Loaded image ID\n"
        elif command[:2] == ["docker", "create"]:
            stdout = "model-verification-container\n"
        elif command[:2] == ["docker", "cp"]:
            destination = Path(command[3])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "test.onnx").write_bytes(b"test-model")
        return subprocess.CompletedProcess(command, 0, stdout, "")

    @staticmethod
    def site_environment_compose() -> str:
        return (
            "services:\n"
            "  engine:\n"
            "    env_file: [/etc/ksi-cctv/engine.env]\n"
            "  web:\n"
            "    env_file: [/etc/ksi-cctv/web.env]\n"
        )

    def backup_recipient(self, directory: Path) -> tuple[Path, Path]:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

        directory.mkdir(parents=True, exist_ok=True)
        private = generate_private_key(public_exponent=65537, key_size=3072)
        private_path = directory / "backup-recipient.pem"
        public_path = directory / "backup-recipient.pub"
        private_path.write_bytes(
            private.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        public_path.write_bytes(
            private.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        )
        os.chmod(private_path, 0o600)
        os.chmod(public_path, 0o644)
        return public_path, private_path

    def declare_file(self, release: Path, relative: str) -> None:
        manifest_path = release / "release-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["files"][relative] = hashlib.sha256((release / relative).read_bytes()).hexdigest()
        refresh_bundle_attestation(
            release,
            manifest["images"],
            release.parent,
            version=manifest["version"],
            profiles=manifest["hardware_profiles"],
        )
        for bundle_file in (
            "evidence/bundle-attestation.json",
            "evidence/bundle-attestation.json.sig",
        ):
            manifest["files"][bundle_file] = hashlib.sha256(
                (release / bundle_file).read_bytes()
            ).hexdigest()
        manifest_path.write_bytes(appliance.canonical_manifest(manifest))

    def test_upgrade_stages_verifies_and_switches_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            first, _ = self.make_release(Path(temp), "v0")
            release, _ = self.make_release(Path(temp), "v1")
            backup_public, _ = self.backup_recipient(Path(temp))
            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(appliance, "run_command", side_effect=self.successful_command):
                installed = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(first),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public), "--confirm", "--allow-unsigned",
                ])
                code = appliance.main(["upgrade", "--root", str(root), "--release-dir", str(release), "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned"])
            layout = appliance.Layout(root)
            self.assertEqual(installed, 0)
            self.assertEqual(code, 0)
            self.assertEqual(layout.current.resolve().name, "v1")
            self.assertTrue(layout.rollback.exists())
            self.assertEqual(
                json.loads((layout.etc / "appliance.json").read_text(encoding="utf-8"))["version"],
                "v1",
            )
            with patch.object(appliance, "run_command", side_effect=self.successful_command):
                rolled_back = appliance.main([
                    "rollback", "--root", str(root), "--profile", "dgx-spark-arm64",
                    "--confirm", "--allow-unsigned",
                ])
            self.assertEqual(rolled_back, 0)
            self.assertEqual(layout.current.resolve().name, "v0")
            self.assertFalse(layout.rollback.exists())
            self.assertEqual(len(list(layout.backups.glob("*.enc"))), 2)
            self.assertEqual(
                json.loads((layout.etc / "appliance.json").read_text(encoding="utf-8"))["version"],
                "v0",
            )

    def test_verify_checks_candidate_bundle_without_mutating_appliance_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp), "candidate")

            code = appliance.main([
                "verify", "--root", str(root), "--release-dir", str(release),
                "--profile", "dgx-spark-arm64", "--allow-unsigned",
            ])

            self.assertEqual(code, 0)
            self.assertFalse(root.exists())

    def test_install_prepares_layout_and_records_profile(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            backup_public, _ = self.backup_recipient(Path(temp))
            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(appliance, "run_command", side_effect=self.successful_command):
                code = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public), "--confirm", "--allow-unsigned",
                ])
            layout = appliance.Layout(root)
            self.assertEqual(code, 0)
            self.assertEqual(layout.current.resolve().name, "v1")
            state = json.loads((layout.etc / "appliance.json").read_text(encoding="utf-8"))
            self.assertEqual(state["profile"], "dgx-spark-arm64")
            self.assertEqual(state["state_schema"], appliance.STATE_SCHEMA_VERSION)
            self.assertEqual((layout.etc / "appliance.json").stat().st_mode & 0o777, 0o600)
            unit = root / "etc" / "systemd" / "system" / appliance.SERVICE
            self.assertTrue(unit.is_file())
            self.assertEqual(unit.stat().st_mode & 0o777, 0o644)

    def test_signed_install_persists_both_external_trust_roots(self):
        with tempfile.TemporaryDirectory() as temp:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
            from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

            root = Path(temp) / "root"
            release, release_public = self.make_release(Path(temp), signed=True)
            backup_public, _ = self.backup_recipient(Path(temp))
            attestation_public = Path(temp) / "ci-attestation.pub"
            assert release_public is not None
            verified_release = release_public.read_bytes()
            verified_attestation = attestation_public.read_bytes()
            verified_backup = backup_public.read_bytes()
            original_stage = appliance.stage_release

            def replace_sources_after_snapshot(*args, **kwargs):
                replacement = Ed25519PrivateKey.generate()
                replacement_bytes = base64.b64encode(replacement.public_key().public_bytes(
                    serialization.Encoding.Raw,
                    serialization.PublicFormat.Raw,
                ))
                release_public.write_bytes(replacement_bytes)
                attestation_public.write_bytes(replacement_bytes)
                replacement_backup = generate_private_key(public_exponent=65537, key_size=3072)
                backup_public.write_bytes(replacement_backup.public_key().public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                ))
                return original_stage(*args, **kwargs)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=self.successful_command
            ), patch.object(
                appliance, "stage_release", side_effect=replace_sources_after_snapshot
            ):
                code = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--public-key", str(release_public),
                    "--attestation-public-key", str(attestation_public), "--confirm",
                ])

            layout = appliance.Layout(root)
            self.assertEqual(code, 0)
            self.assertEqual(layout.release_key.read_bytes(), verified_release + b"\n")
            self.assertEqual(layout.attestation_key.read_bytes(), verified_attestation + b"\n")
            self.assertEqual(layout.backup_recipient.read_bytes(), verified_backup)
            self.assertEqual(layout.trust_roots.stat().st_mode & 0o777, 0o700)
            self.assertEqual(layout.release_key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(layout.attestation_key.stat().st_mode & 0o777, 0o600)

    def test_v1_rejects_in_place_trust_key_rotation(self):
        with tempfile.TemporaryDirectory() as temp:
            from argparse import Namespace

            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            layout.trust_roots.mkdir(mode=0o700)
            existing = Ed25519PrivateKey.generate()
            replacement = Ed25519PrivateKey.generate()
            for path, key in ((layout.release_key, existing), (Path(temp) / "replacement.pub", replacement)):
                path.write_bytes(base64.b64encode(key.public_key().public_bytes(
                    serialization.Encoding.Raw, serialization.PublicFormat.Raw
                )))
                os.chmod(path, 0o644)
            with self.assertRaisesRegex(appliance.ApplianceError, "rotation is not supported"):
                appliance.trusted_key(
                    Namespace(public_key=str(Path(temp) / "replacement.pub")), layout
                )

    def test_production_root_rejects_unsigned_escape_hatch(self):
        self.assertEqual(appliance.main([
            "verify", "--root", "/", "--release-dir", "/nonexistent", "--allow-unsigned"
        ]), 2)

    def test_custom_root_rejects_unsigned_mutation_with_host_backend(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            backup_public, _ = self.backup_recipient(Path(temp))

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "HOST_RUN_COMMAND", appliance.run_command
            ):
                code = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public), "--confirm", "--allow-unsigned",
                ])

            self.assertEqual(code, 2)
            self.assertFalse(root.exists())

    def test_install_reloads_and_enables_signed_systemd_unit_before_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            backup_public, _ = self.backup_recipient(Path(temp))
            commands = []

            def run(command, timeout=None):
                commands.append(command)
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(appliance, "run_command", side_effect=run):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public), "--confirm", "--allow-unsigned",
                ]), 0)

            self.assertLess(commands.index(["systemctl", "daemon-reload"]), commands.index(["systemctl", "restart", appliance.SERVICE]))
            self.assertLess(commands.index(["systemctl", "enable", appliance.SERVICE]), commands.index(["systemctl", "restart", appliance.SERVICE]))

    def test_failed_first_install_cleans_staged_release_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            backup_public, _ = self.backup_recipient(Path(temp))
            fail_health = True

            def run(command, timeout=None):
                nonlocal fail_health
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND) and fail_health:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                first = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ])
                fail_health = False
                second = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ])

            layout = appliance.Layout(root)
            self.assertEqual(first, 2)
            self.assertEqual(second, 0)
            self.assertEqual(layout.current.resolve().name, "v1")
            self.assertFalse(layout.transaction.exists())
            self.assertFalse((root / "etc" / "systemd" / "system" / appliance.SERVICE).is_symlink())

    def test_pre_activation_install_failure_removes_stage_and_allows_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            backup_public, _ = self.backup_recipient(Path(temp))
            base = [
                "install", "--root", str(root), "--release-dir", str(release),
                "--profile", "dgx-spark-arm64", "--backup-recipient-public-key", str(backup_public),
                "--confirm", "--allow-unsigned",
            ]
            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=self.successful_command
            ):
                self.assertEqual(appliance.main(base), 2)
                self.assertFalse((appliance.Layout(root).releases / "v1").exists())
                retry = [*base, "--site-env", str(Path(temp) / "site.env")]
                self.assertEqual(appliance.main(retry), 0)

    def test_failed_first_install_retains_recovery_state_when_service_disable_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            backup_public, _ = self.backup_recipient(Path(temp))

            def run(command, timeout=None):
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND) or command == ["systemctl", "disable", appliance.SERVICE]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                result = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ])

            layout = appliance.Layout(root)
            self.assertEqual(result, 2)
            self.assertTrue((layout.releases / "v1").is_dir())
            self.assertTrue((layout.etc / "appliance.json").is_file())
            self.assertTrue((root / "etc" / "systemd" / "system" / appliance.SERVICE).is_file())

    def test_failed_first_install_stop_retains_journal_and_signed_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            backup_public, _ = self.backup_recipient(Path(temp))

            def run(command, timeout=None):
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND):
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command == ["systemctl", "stop", appliance.SERVICE]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                result = appliance.main([
                    "install", "--root", str(root), "--release-dir", str(release),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ])

            layout = appliance.Layout(root)
            self.assertEqual(result, 2)
            self.assertEqual(layout.current.resolve().name, "v1")
            self.assertTrue((layout.releases / "v1").is_dir())
            self.assertEqual(
                json.loads(layout.transaction.read_text(encoding="utf-8"))["phase"],
                "recovery_failed",
            )

    def test_install_seeds_defaults_once_and_upgrade_preserves_stable_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            first, _ = self.make_release(Path(temp), "v1")
            backup_public, _ = self.backup_recipient(Path(temp))
            defaults = first / "defaults" / "config"
            defaults.mkdir(parents=True, exist_ok=True)
            shutil.copy2(MODULE.parents[1] / "config" / "default.yaml", defaults / "default.yaml")
            self.declare_file(first, "defaults/config/default.yaml")
            second, _ = self.make_release(Path(temp), "v2")
            next_defaults = second / "defaults" / "config"
            next_defaults.mkdir(parents=True, exist_ok=True)
            shutil.copy2(MODULE.parents[1] / "config" / "default.yaml", next_defaults / "default.yaml")
            self.declare_file(second, "defaults/config/default.yaml")
            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(appliance, "run_command", side_effect=self.successful_command):
                self.assertEqual(appliance.main(["install", "--root", str(root), "--release-dir", str(first), "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"), "--backup-recipient-public-key", str(backup_public), "--confirm", "--allow-unsigned"]), 0)
                layout = appliance.Layout(root)
                (layout.config / "default.yaml").write_text("operator: chosen\n", encoding="utf-8")
                self.assertEqual(appliance.main(["upgrade", "--root", str(root), "--release-dir", str(second), "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned"]), 0)
            self.assertEqual((layout.config / "default.yaml").read_text(encoding="utf-8"), "operator: chosen\n")
            self.assertTrue(layout.cameras.is_dir())
            self.assertEqual(layout.cameras.stat().st_mode & 0o777, 0o700)

    def test_destructive_actions_need_confirmation_without_subprocess(self):
        with patch.object(appliance.subprocess, "run") as run:
            code = appliance.main(["start", "--root", "/unused"])
        self.assertEqual(code, 2)
        run.assert_not_called()

    def test_status_is_read_only_and_uses_no_shell(self):
        completed = subprocess.CompletedProcess(["systemctl"], 3, "inactive\n", "API_KEY=secret")
        with patch.object(appliance.subprocess, "run", return_value=completed) as run:
            code = appliance.main(["status", "--root", "/unused"])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_args.kwargs["shell"], False)
        self.assertEqual(run.call_args.args[0], ["systemctl", "is-active", appliance.SERVICE])

    def test_restore_validation_rejects_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "unsafe.tar"
            import io
            import tarfile
            with tarfile.open(archive, "w") as tar:
                info = tarfile.TarInfo("../../etc/passwd")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
            self.assertEqual(appliance.main(["restore", "--archive", str(archive)]), 2)

    def test_restore_validation_rejects_excessive_member_count(self):
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "large.tar"
            import io
            import tarfile

            with tarfile.open(archive, "w") as tar:
                for index in range(appliance.MAX_BACKUP_MEMBERS + 1):
                    info = tarfile.TarInfo(f"etc/ksi-cctv/{index}")
                    info.size = 0
                    tar.addfile(info, io.BytesIO())
            with self.assertRaises(appliance.ApplianceError):
                appliance.validate_backup(archive)

    def test_restore_validation_rejects_empty_partial_and_duplicate_archives(self):
        with tempfile.TemporaryDirectory() as temp:
            empty = Path(temp) / "empty.tar"
            with tarfile.open(empty, "w"):
                pass
            with self.assertRaisesRegex(appliance.ApplianceError, "manifest is missing"):
                appliance.validate_backup(empty)

            partial = Path(temp) / "partial.tar"
            with tarfile.open(partial, "w") as tar:
                directory = tarfile.TarInfo("etc/ksi-cctv")
                directory.type = tarfile.DIRTYPE
                directory.mode = 0o700
                tar.addfile(directory)
                import io
                manifest = json.dumps({
                    "schema_version": 1,
                    "roots": ["etc/ksi-cctv", "var/lib/ksi-cctv", "var/log/ksi-cctv"],
                    "entries": {"etc/ksi-cctv": {"mode": 0o700, "type": "directory"}},
                }).encode()
                info = tarfile.TarInfo("backup-manifest.json")
                info.mode = 0o600
                info.size = len(manifest)
                tar.addfile(info, io.BytesIO(manifest))
            with self.assertRaisesRegex(appliance.ApplianceError, "complete archive payload"):
                appliance.validate_backup(partial)

            duplicate = Path(temp) / "duplicate.tar"
            with tarfile.open(duplicate, "w") as tar:
                for _ in range(2):
                    directory = tarfile.TarInfo("etc/ksi-cctv")
                    directory.type = tarfile.DIRTYPE
                    tar.addfile(directory)
            with self.assertRaisesRegex(appliance.ApplianceError, "duplicate or noncanonical"):
                appliance.validate_backup(duplicate)

    def test_restore_replaces_config_after_safety_backup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, backup_private = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            (layout.etc / "site.conf").write_text("before", encoding="utf-8")
            (layout.data / "state.json").write_text("old", encoding="utf-8")
            archive = appliance.create_backup(layout)
            (layout.etc / "site.conf").write_text("changed", encoding="utf-8")
            (layout.data / "state.json").write_text("changed", encoding="utf-8")

            code = appliance.main([
                "restore", "--root", str(root), "--archive", str(archive),
                "--backup-recipient-private-key", str(backup_private), "--confirm",
            ])

            self.assertEqual(code, 0)
            self.assertEqual((layout.etc / "site.conf").read_text(encoding="utf-8"), "before")
            self.assertEqual((layout.data / "state.json").read_text(encoding="utf-8"), "old")
            self.assertFalse(layout.transaction.exists())
            self.assertEqual(list((root / "var" / "tmp").glob("ksi-cctv-restore-*")), [])
            self.assertEqual(list((root / "var" / "tmp").glob("ksi-cctv-backup-decrypt-*")), [])

    def test_restore_requires_confirmation_before_decryption(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            args = appliance.parser().parse_args([
                "restore",
                "--root", str(layout.root),
                "--archive", str(Path(temp) / "backup.enc"),
                "--backup-recipient-private-key", str(Path(temp) / "private.pem"),
            ])
            with (
                patch.object(appliance, "_decrypt_backup") as decrypt,
                self.assertRaisesRegex(appliance.ApplianceError, "--confirm"),
            ):
                appliance.command_restore(args, layout)
            decrypt.assert_not_called()

    def test_restore_validate_only_uses_ephemeral_run_staging_without_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, backup_private = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            archive = appliance.create_backup(layout)

            self.assertEqual(appliance.main([
                "restore", "--root", str(root), "--archive", str(archive),
                "--backup-recipient-private-key", str(backup_private), "--validate-only",
            ]), 0)

            self.assertFalse(layout.sensitive_staging.exists())
            self.assertEqual(
                list((root / "run" / "ksi-cctv-validation").glob("backup-*")), []
            )

    def test_restore_validation_rejects_persistent_production_run(self):
        layout = appliance.Layout(Path("/"))
        mountinfo = "1 0 0:1 / / rw,relatime - ext4 /dev/root rw\n"
        with (
            patch.object(Path, "read_text", return_value=mountinfo),
            self.assertRaisesRegex(appliance.ApplianceError, "tmpfs or ramfs"),
        ):
            appliance.require_ephemeral_production_run(layout)

        nested_mounts = (
            "1 0 0:1 / /run rw - tmpfs tmpfs rw\n"
            "2 1 8:1 / /run/ksi-cctv-validation rw - ext4 /dev/sda1 rw\n"
        )
        self.assertEqual(
            appliance._mount_filesystem_type(
                Path("/run/ksi-cctv-validation/backup"), nested_mounts
            ),
            "ext4",
        )

    def test_backup_is_encrypted_and_requires_the_external_recipient_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, backup_private = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            (layout.etc / "site.conf").write_text("KSI_CCTV_API_KEY=not-in-plaintext", encoding="utf-8")

            archive = appliance.create_backup(layout)

            self.assertTrue(archive.name.endswith(".tar.gz.enc"))
            self.assertNotIn(b"not-in-plaintext", archive.read_bytes())
            statement_path, signature_path = appliance.backup_provenance_paths(archive)
            self.assertTrue(statement_path.is_file())
            self.assertTrue(signature_path.is_file())
            self.assertEqual(statement_path.stat().st_mode & 0o777, 0o600)
            with tempfile.TemporaryDirectory() as decrypted:
                plaintext = Path(decrypted) / "backup.tar.gz"
                appliance._decrypt_backup(archive, plaintext, backup_private)
                appliance.validate_backup(plaintext)
                with tarfile.open(plaintext, "r:*") as tar:
                    content = tar.extractfile("etc/ksi-cctv/site.conf")
                    assert content is not None
                    self.assertIn(b"not-in-plaintext", content.read())

    def test_backup_provenance_rejects_forgery_cross_appliance_and_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, backup_private = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            archive = appliance.create_backup(layout)
            provenance = appliance.verify_backup_provenance(
                layout, archive, enforce_replay=True, allow_replay=False
            )

            staged_dir = Path(temp) / "private-staging"
            staged_dir.mkdir(mode=0o700)
            original_archive = archive.read_bytes()
            staged_archive = appliance.stage_backup_inputs(archive, staged_dir)
            archive.write_bytes(b"substituted-after-private-copy")
            appliance.verify_backup_provenance(
                layout, staged_archive, enforce_replay=True, allow_replay=False
            )
            archive.write_bytes(original_archive)

            plaintext = Path(temp) / "plaintext.tar.gz"
            appliance._decrypt_backup(archive, plaintext, backup_private)
            forged = layout.backups / "forged.tar.gz.enc"
            appliance._encrypt_backup(plaintext, forged, layout.backup_recipient)
            with self.assertRaisesRegex(appliance.ApplianceError, "provenance"):
                appliance.verify_backup_provenance(
                    layout, forged, enforce_replay=True, allow_replay=False
                )

            other = appliance.Layout(Path(temp) / "other-root")
            appliance.prepare_layout(other)
            appliance.install_backup_recipient(other, str(backup_public))
            appliance.ensure_backup_signing_identity(other)
            copied = other.backups / archive.name
            copied.write_bytes(archive.read_bytes())
            source_statement, source_signature = appliance.backup_provenance_paths(archive)
            copied_statement, copied_signature = appliance.backup_provenance_paths(copied)
            copied_statement.write_bytes(source_statement.read_bytes())
            copied_signature.write_bytes(source_signature.read_bytes())
            with self.assertRaisesRegex(appliance.ApplianceError, "signature|trust identity"):
                appliance.verify_backup_provenance(
                    other, copied, enforce_replay=True, allow_replay=False
                )

            appliance.record_restored_backup_sequence(layout, provenance["sequence"])
            with self.assertRaisesRegex(appliance.ApplianceError, "replay"):
                appliance.verify_backup_provenance(
                    layout, archive, enforce_replay=True, allow_replay=False
                )
            appliance.verify_backup_provenance(
                layout, archive, enforce_replay=True, allow_replay=True
            )

    def test_backup_identity_recovers_precommit_staging_as_one_atomic_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            partial = layout.backup_identity.with_name(
                f".{layout.backup_identity.name}.{os.getpid()}.tmp"
            )
            partial.mkdir()
            (partial / "partial-key").write_bytes(b"incomplete")

            appliance.ensure_backup_signing_identity(layout)

            self.assertFalse(partial.exists())
            self.assertEqual(layout.backup_identity.stat().st_mode & 0o777, 0o700)
            for path in (
                layout.appliance_id,
                layout.backup_signing_private,
                layout.backup_signing_public,
                layout.backup_sequence,
                layout.last_restore_sequence,
            ):
                self.assertTrue(path.is_file())

    def test_backup_decryption_rejects_a_non_recipient_private_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, _ = self.backup_recipient(Path(temp))
            _, wrong_private = self.backup_recipient(Path(temp) / "other")
            appliance.install_backup_recipient(layout, str(backup_public))
            archive = appliance.create_backup(layout)

            with self.assertRaises(appliance.ApplianceError):
                appliance._decrypt_backup(archive, Path(temp) / "wrong.tar.gz", wrong_private)

    def test_backup_decryption_rejects_an_exposed_or_symlinked_private_key(self):
        with tempfile.TemporaryDirectory() as temp:
            _public, private = self.backup_recipient(Path(temp))
            os.chmod(private, 0o644)
            with self.assertRaisesRegex(appliance.ApplianceError, "owner-only regular file"):
                appliance._load_backup_private_key(private)
            os.chmod(private, 0o600)
            linked = Path(temp) / "linked-private.pem"
            linked.symlink_to(private)
            with self.assertRaisesRegex(appliance.ApplianceError, "owner-only regular file"):
                appliance._load_backup_private_key(linked)

    def test_backup_snapshots_a_wal_database_during_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, backup_private = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            database = layout.data / "events.db"
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE records (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO records(value) VALUES ('seed')")
            connection.commit()
            stop = threading.Event()
            errors: list[Exception] = []

            def write_records():
                writer = sqlite3.connect(database, timeout=5.0)
                try:
                    while not stop.is_set():
                        writer.execute("INSERT INTO records(value) VALUES ('active')")
                        writer.commit()
                        time.sleep(0.001)
                except Exception as exc:  # pragma: no cover - asserted after thread joins
                    errors.append(exc)
                finally:
                    writer.close()

            thread = threading.Thread(target=write_records)
            thread.start()
            try:
                archive = appliance.create_backup(layout)
            finally:
                stop.set()
                thread.join(timeout=5)
                connection.close()
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

            with tempfile.TemporaryDirectory() as extracted:
                plaintext = Path(extracted) / "backup.tar.gz"
                appliance._decrypt_backup(archive, plaintext, backup_private)
                with tarfile.open(plaintext, "r:*") as tar:
                    members = tar.getnames()
                    tar.extractall(extracted, filter="data")
                restored = Path(extracted) / "var" / "lib" / "ksi-cctv" / "events.db"
                self.assertIn("var/lib/ksi-cctv/events.db", members)
                self.assertNotIn("var/lib/ksi-cctv/events.db-wal", members)
                self.assertNotIn("var/lib/ksi-cctv/events.db-shm", members)
                with sqlite3.connect(restored) as restored_connection:
                    self.assertEqual(restored_connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    self.assertGreater(restored_connection.execute("SELECT COUNT(*) FROM records").fetchone()[0], 0)

    def test_mask_secrets(self):
        self.assertEqual(appliance.mask_secrets("TOKEN=abc PASSWORD: xyz"), "TOKEN=*** PASSWORD: ***")

    def test_release_version_cannot_escape_staging_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp), "v1")
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["version"] = "../escape"
            manifest_path.write_bytes(appliance.canonical_manifest(manifest))
            with self.assertRaises(appliance.ApplianceError):
                appliance.verify_release(release, allow_unsigned=True)

    def test_backup_permissions_are_restricted_for_existing_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp))
            appliance.prepare_layout(layout)
            backup_public, _ = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            os.chmod(layout.backups, 0o777)
            archive = appliance.create_backup(layout)
            self.assertEqual(layout.backups.stat().st_mode & 0o777, 0o700)
            self.assertEqual(archive.stat().st_mode & 0o777, 0o600)

    def test_backup_rejects_symlinks_and_removes_partial_plaintext(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, _ = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            outside = Path(temp) / "host-secret"
            outside.write_text("must-not-be-backed-up", encoding="utf-8")
            (layout.data / "unsafe-link").symlink_to(outside)

            with self.assertRaisesRegex(appliance.ApplianceError, "must not contain symlinks"):
                appliance.create_backup(layout)

            self.assertEqual(list(layout.backups.glob("*.enc")), [])
            self.assertEqual(list(layout.backups.glob("*.tmp")), [])
            self.assertEqual(list(layout.backups.glob("ksi-cctv-backup-*")), [])

    def test_backup_rejects_special_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            backup_public, _ = self.backup_recipient(Path(temp))
            appliance.install_backup_recipient(layout, str(backup_public))
            os.mkfifo(layout.log / "unsafe-pipe")

            with self.assertRaisesRegex(appliance.ApplianceError, "special file"):
                appliance.create_backup(layout)

            self.assertEqual(list(layout.backups.glob("*.enc")), [])

    def test_managed_backup_stops_runtime_during_snapshot_and_restarts_it(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            archive = layout.backups / "backup.enc"
            args = appliance.parser().parse_args(["backup", "--root", str(layout.root), "--confirm"])
            with (
                patch.object(appliance, "current_release", return_value=layout.releases / "v1"),
                patch.object(appliance, "service_is_active", return_value=True),
                patch.object(appliance, "service_action", side_effect=[0, 0]) as service,
                patch.object(appliance, "create_backup", return_value=archive),
                patch.object(appliance, "verify_active_health") as health,
            ):
                result, managed, was_active = appliance.create_quiesced_backup(
                    args, layout, keep_stopped=False
                )

            self.assertEqual(result, archive)
            self.assertTrue(managed)
            self.assertTrue(was_active)
            self.assertEqual([call.args[0] for call in service.call_args_list], ["stop", "restart"])
            health.assert_called_once_with(args, layout)

    def test_managed_backup_quiesces_but_does_not_restart_an_inactive_service(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            archive = layout.backups / "backup.enc"
            args = appliance.parser().parse_args(["backup", "--root", str(layout.root), "--confirm"])
            with (
                patch.object(appliance, "current_release", return_value=layout.releases / "v1"),
                patch.object(appliance, "service_is_active", return_value=False),
                patch.object(appliance, "service_action", return_value=0) as service,
                patch.object(appliance, "create_backup", return_value=archive),
                patch.object(appliance, "verify_active_health") as health,
            ):
                result, managed, was_active = appliance.create_quiesced_backup(
                    args, layout, keep_stopped=False
                )

            self.assertEqual(result, archive)
            self.assertTrue(managed)
            self.assertFalse(was_active)
            service.assert_called_once_with("stop")
            health.assert_not_called()
            self.assertFalse(layout.transaction.exists())

    def test_valid_signed_release_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            release, public_key = self.make_release(Path(temp), signed=True)
            appliance.verify_release(
                release,
                public_key=public_key,
                attestation_public_key=Path(temp) / "ci-attestation.pub",
            )

    def test_release_rejects_signed_noncanonical_host_runtime_script(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            health = release / "bin" / "activation_health.py"
            health.write_text("raise SystemExit(0)\n", encoding="utf-8")
            self.declare_file(release, "bin/activation_health.py")

            with self.assertRaisesRegex(appliance.ApplianceError, "does not match gated source"):
                appliance.verify_release(release, allow_unsigned=True)

    def test_signed_release_rejects_a_public_key_shipped_in_the_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            release, public_key = self.make_release(Path(temp), signed=True)
            assert public_key is not None
            bundled_key = release / "release-signing.pub"
            bundled_key.write_bytes(public_key.read_bytes())

            with self.assertRaisesRegex(appliance.ApplianceError, "must be outside"):
                appliance.verify_signature(release, appliance.load_manifest(release), bundled_key, allow_unsigned=False)

    def test_signed_release_rejects_reuse_of_ci_key_as_release_key(self):
        with tempfile.TemporaryDirectory() as temp:
            from cryptography.hazmat.primitives import serialization

            release, _ = self.make_release(Path(temp))
            manifest = appliance.load_manifest(release)
            private = serialization.load_pem_private_key(
                (Path(temp) / "ci-attestation.pem").read_bytes(), password=None
            )
            (release / "release-manifest.sig").write_bytes(
                base64.b64encode(private.sign(appliance.canonical_manifest(manifest)))
            )
            public_key = Path(temp) / "ci-attestation.pub"

            with self.assertRaisesRegex(appliance.ApplianceError, "must be distinct"):
                appliance.verify_release(
                    release,
                    public_key=public_key,
                    attestation_public_key=public_key,
                )

    def test_tampered_signed_manifest_and_payload_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            release, public_key = self.make_release(Path(temp), signed=True)
            manifest = json.loads((release / "release-manifest.json").read_text(encoding="utf-8"))
            manifest["version"] = "v2"
            (release / "release-manifest.json").write_bytes(appliance.canonical_manifest(manifest))
            with self.assertRaises(appliance.ApplianceError):
                appliance.verify_release(
                    release,
                    public_key=public_key,
                    attestation_public_key=Path(temp) / "ci-attestation.pub",
                )
            release, public_key = self.make_release(Path(temp), "payload", signed=True)
            (release / "compose.yaml").write_text("services: {changed: {}}\n", encoding="utf-8")
            with self.assertRaises(appliance.ApplianceError):
                appliance.verify_release(
                    release,
                    public_key=public_key,
                    attestation_public_key=Path(temp) / "ci-attestation.pub",
                )

    def test_absent_signature_is_rejected_unless_unsafe_escape_hatch_is_used(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            with self.assertRaises(appliance.ApplianceError):
                appliance.verify_release(release)
            appliance.verify_release(release, allow_unsigned=True)

    def test_release_rejects_arbitrary_signed_health_command(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["healthcheck"]["command"] = ["/bin/sh", "-c", "touch /tmp/untrusted"]
            manifest_path.write_bytes(appliance.canonical_manifest(manifest))

            with self.assertRaisesRegex(appliance.ApplianceError, "activation health contract"):
                appliance.verify_release(release, allow_unsigned=True)

    def test_release_rejects_undeclared_extra_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            (release / ".env").write_text("COMPOSE_FILE=malicious.yaml\n", encoding="utf-8")

            with self.assertRaises(appliance.ApplianceError):
                appliance.verify_release(release, allow_unsigned=True)

    def test_release_manifest_rejects_unknown_fields_and_empty_profiles(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            manifest_path = release / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["unexpected"] = True
            manifest_path.write_bytes(appliance.canonical_manifest(manifest))
            with self.assertRaisesRegex(appliance.ApplianceError, "unexpected"):
                appliance.verify_release(release, allow_unsigned=True)

            del manifest["unexpected"]
            manifest["hardware_profiles"] = []
            manifest_path.write_bytes(appliance.canonical_manifest(manifest))
            with self.assertRaisesRegex(appliance.ApplianceError, "hardware profiles"):
                appliance.verify_release(release, allow_unsigned=True)

            manifest["hardware_profiles"] = [["dgx-spark-arm64"]]
            manifest_path.write_bytes(appliance.canonical_manifest(manifest))
            with self.assertRaisesRegex(appliance.ApplianceError, "hardware profiles"):
                appliance.load_manifest(release)

    def test_release_manifest_and_signature_require_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as temp:
            release, public_key = self.make_release(Path(temp), signed=True)
            manifest_path = release / "release-manifest.json"
            raw = manifest_path.read_bytes()
            manifest_path.write_bytes(raw + b" ")
            with self.assertRaisesRegex(appliance.ApplianceError, "canonical JSON"):
                appliance.load_manifest(release)

            manifest_path.write_bytes(b'{"version":"other",' + raw[1:])
            with self.assertRaisesRegex(appliance.ApplianceError, "duplicate JSON key"):
                appliance.load_manifest(release)

            manifest_path.write_bytes(raw)
            signature = release / "release-manifest.sig"
            signature.write_bytes(signature.read_bytes() + b"\n")
            with self.assertRaisesRegex(appliance.ApplianceError, "signature verification"):
                appliance.verify_signature(
                    release,
                    appliance.load_manifest(release),
                    public_key,
                    allow_unsigned=False,
                )

    def test_release_rejects_extra_or_privileged_compose_services(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            compose = release / "compose.yaml"
            compose.write_text(
                compose.read_text(encoding="utf-8").replace(
                    "    user: \"1000:1000\"\n",
                    "    user: \"1000:1000\"\n    privileged: true\n",
                    1,
                ),
                encoding="utf-8",
            )
            self.declare_file(release, "compose.yaml")

            with self.assertRaisesRegex(appliance.ApplianceError, "contract is invalid"):
                appliance.verify_release(release, allow_unsigned=True)

            release, _ = self.make_release(Path(temp), "typed")
            compose = release / "compose.yaml"
            compose.write_text(
                compose.read_text(encoding="utf-8").replace("    read_only: true", "    read_only: 1", 1),
                encoding="utf-8",
            )
            self.declare_file(release, "compose.yaml")
            with self.assertRaisesRegex(appliance.ApplianceError, "contract is invalid"):
                appliance.verify_release(release, allow_unsigned=True)

    def test_release_rejects_quoted_extra_compose_service(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            compose = release / "compose.yaml"
            compose.write_text(
                compose.read_text(encoding="utf-8").replace(
                    "networks:\n",
                    '  "evil":\n    "image": "sha256:' + "e" * 64
                    + '"\n    "privileged": true\nnetworks:\n',
                ),
                encoding="utf-8",
            )
            self.declare_file(release, "compose.yaml")

            with self.assertRaisesRegex(appliance.ApplianceError, "service set is invalid"):
                appliance.verify_release(release, allow_unsigned=True)

    def test_release_rejects_weakened_caddy_or_injected_systemd_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            caddy = release / "Caddyfile"
            caddy.write_text(
                caddy.read_text(encoding="utf-8").replace(
                    '\t\tX-Frame-Options "DENY"\n', ""
                ),
                encoding="utf-8",
            )
            self.declare_file(release, "Caddyfile")
            with self.assertRaisesRegex(appliance.ApplianceError, "Caddyfile"):
                appliance.verify_release(release, allow_unsigned=True)

            release, _ = self.make_release(Path(temp), "unit")
            unit = release / "systemd" / appliance.SERVICE
            unit.write_text(
                unit.read_text(encoding="utf-8").replace(
                    "ExecStart=", "ExecStartPre=/bin/sh -c id\nExecStart=", 1
                ),
                encoding="utf-8",
            )
            self.declare_file(release, f"systemd/{appliance.SERVICE}")
            with self.assertRaisesRegex(appliance.ApplianceError, "systemd unit"):
                appliance.verify_release(release, allow_unsigned=True)

            release, _ = self.make_release(Path(temp), "gateway")
            mediamtx = release / "mediamtx.yml"
            mediamtx.write_text(
                mediamtx.read_text(encoding="utf-8").replace("webrtc: no", "webrtc: yes"),
                encoding="utf-8",
            )
            self.declare_file(release, "mediamtx.yml")
            with self.assertRaisesRegex(appliance.ApplianceError, "HLS-only"):
                appliance.verify_release(release, allow_unsigned=True)

    def test_stage_normalizes_modes_and_rechecks_before_privileged_use(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            for path in [release, *release.rglob("*")]:
                os.chmod(path, 0o777)
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            manifest, staged = appliance.stage_release(
                layout, release, "dgx-spark-arm64", None, True
            )

            self.assertEqual(staged.stat().st_mode & 0o777, 0o555)
            self.assertEqual((staged / "compose.yaml").stat().st_mode & 0o777, 0o444)
            os.chmod(staged / "compose.yaml", 0o644)
            (staged / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
            with patch.object(appliance, "run_command") as run, self.assertRaisesRegex(
                appliance.ApplianceError, "permissions are unsafe|checksum verification failed"
            ):
                appliance.activate_release(layout, staged, manifest, 10)
            run.assert_not_called()

    def test_activation_failure_restores_previous_release_and_restarts_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            _, old_staged = appliance.stage_release(layout, old, "dgx-spark-arm64", None, True)
            new_manifest, new_staged = appliance.stage_release(layout, new, "dgx-spark-arm64", None, True)
            appliance.atomic_switch(layout.current, old_staged)
            calls = []

            def commands(command, timeout=None):
                calls.append(command)
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND):
                    return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "run_command", side_effect=commands), self.assertRaises(appliance.ApplianceError):
                appliance.activate_release(layout, new_staged, new_manifest, 10)
            self.assertEqual(layout.current.resolve(), old_staged)
            self.assertEqual(calls.count(["systemctl", "restart", appliance.SERVICE]), 2)

    def test_failed_upgrade_restores_previous_signed_systemd_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            old_unit = old / "systemd" / appliance.SERVICE
            new_unit = new / "systemd" / appliance.SERVICE
            old_unit.write_text(old_unit.read_text(encoding="utf-8") + "# old\n", encoding="utf-8")
            new_unit.write_text(new_unit.read_text(encoding="utf-8") + "# new\n", encoding="utf-8")
            self.declare_file(old, f"systemd/{appliance.SERVICE}")
            self.declare_file(new, f"systemd/{appliance.SERVICE}")
            new_manifest = json.loads((new / "release-manifest.json").read_text(encoding="utf-8"))
            new_manifest["healthcheck"]["command"] = list(appliance.ACTIVATION_HEALTH_COMMAND)
            (new / "release-manifest.json").write_bytes(appliance.canonical_manifest(new_manifest))
            backup_public, _ = self.backup_recipient(Path(temp))

            health_attempts = 0

            def run(command, timeout=None):
                nonlocal health_attempts
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND):
                    health_attempts += 1
                    if health_attempts == 2:
                        return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                self.assertEqual(appliance.main([
                    "upgrade", "--root", str(root), "--release-dir", str(new),
                    "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                ]), 2)

            layout = appliance.Layout(root)
            installed_unit = root / "etc" / "systemd" / "system" / appliance.SERVICE
            self.assertEqual(layout.current.resolve().name, "old")
            self.assertEqual(installed_unit.read_text(encoding="utf-8"), old_unit.read_text(encoding="utf-8"))
            self.assertEqual(json.loads((layout.etc / "appliance.json").read_text(encoding="utf-8"))["version"], "old")

    def test_upgrade_backup_failure_restarts_previous_service_and_removes_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            calls = []

            def run(command, timeout=None):
                calls.append(command)
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                with patch.object(appliance, "create_backup", side_effect=OSError("disk full")):
                    self.assertEqual(appliance.main([
                        "upgrade", "--root", str(root), "--release-dir", str(new),
                        "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                    ]), 2)

            layout = appliance.Layout(root)
            self.assertEqual(layout.current.resolve().name, "old")
            self.assertFalse((layout.releases / "new").exists())
            self.assertFalse(layout.transaction.exists())
            self.assertIn(["systemctl", "restart", appliance.SERVICE], calls)
            self.assertEqual(json.loads((layout.etc / "appliance.json").read_text())["version"], "old")

    def test_upgrade_stop_failure_recovers_previous_and_clears_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            layout = appliance.Layout(root)
            fail_stop = False

            def run(command, timeout=None):
                if fail_stop and command == ["systemctl", "stop", appliance.SERVICE]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                fail_stop = True
                self.assertEqual(appliance.main([
                    "upgrade", "--root", str(root), "--release-dir", str(new),
                    "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                ]), 2)

            self.assertEqual(layout.current.resolve().name, "old")
            self.assertFalse((layout.releases / "new").exists())
            self.assertFalse(layout.transaction.exists())

    def test_upgrade_metadata_write_failure_restarts_previous_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            layout = appliance.Layout(root)
            calls = []
            original_write = appliance.write_json_0600

            def run(command, timeout=None):
                calls.append(command)
                return self.successful_command(command, timeout)

            def fail_rollback_metadata(path, value):
                if path == layout.rollback:
                    raise OSError("disk full")
                return original_write(path, value)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                with patch.object(appliance, "write_json_0600", side_effect=fail_rollback_metadata):
                    self.assertEqual(appliance.main([
                        "upgrade", "--root", str(root), "--release-dir", str(new),
                        "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                    ]), 2)

            self.assertEqual(layout.current.resolve().name, "old")
            self.assertFalse((layout.releases / "new").exists())
            self.assertIn(["systemctl", "restart", appliance.SERVICE], calls)

    def test_upgrade_pre_switch_failure_restarts_previous_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            layout = appliance.Layout(root)
            calls = []

            def run(command, timeout=None):
                calls.append(command)
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                with patch.object(
                    appliance, "activate_release", side_effect=appliance.ApplianceError("pre-switch validation")
                ):
                    self.assertEqual(appliance.main([
                        "upgrade", "--root", str(root), "--release-dir", str(new),
                        "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                    ]), 2)

            self.assertEqual(layout.current.resolve().name, "old")
            self.assertFalse((layout.releases / "new").exists())
            self.assertFalse(layout.transaction.exists())
            self.assertGreaterEqual(calls.count(["systemctl", "restart", appliance.SERVICE]), 2)

    def test_failed_candidate_that_mutates_data_leaves_service_stopped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            layout = appliance.Layout(root)
            calls = []
            health_attempts = 0

            def run(command, timeout=None):
                nonlocal health_attempts
                calls.append(command)
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND):
                    health_attempts += 1
                    if health_attempts == 2:
                        (layout.data / "candidate-write").write_text("changed", encoding="utf-8")
                        return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                self.assertEqual(appliance.main([
                    "upgrade", "--root", str(root), "--release-dir", str(new),
                    "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                ]), 2)

            self.assertEqual(layout.current.resolve().name, "old")
            self.assertTrue((layout.data / "candidate-write").is_file())
            self.assertEqual(
                json.loads(layout.transaction.read_text(encoding="utf-8"))["phase"],
                "data_changed",
            )
            self.assertEqual(calls.count(["systemctl", "stop", appliance.SERVICE]), 2)
            self.assertEqual(calls.count(["systemctl", "restart", appliance.SERVICE]), 2)
            self.assertEqual(json.loads((layout.etc / "appliance.json").read_text())["version"], "old")

    def test_unreadable_candidate_state_is_treated_as_changed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            layout = appliance.Layout(root)
            health_attempts = 0

            def run(command, timeout=None):
                nonlocal health_attempts
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND):
                    health_attempts += 1
                    if health_attempts == 2:
                        (layout.data / "unsafe-link").symlink_to("/tmp")
                        return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                self.assertEqual(appliance.main([
                    "upgrade", "--root", str(root), "--release-dir", str(new),
                    "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                ]), 2)

            self.assertEqual(layout.current.resolve().name, "old")
            self.assertEqual(
                json.loads(layout.transaction.read_text(encoding="utf-8"))["phase"],
                "data_changed",
            )

    def test_failed_candidate_permission_change_leaves_service_stopped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            layout = appliance.Layout(root)
            health_attempts = 0

            def run(command, timeout=None):
                nonlocal health_attempts
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND):
                    health_attempts += 1
                    if health_attempts == 2:
                        os.chmod(layout.config, 0o777)
                        return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=run
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                self.assertEqual(appliance.main([
                    "upgrade", "--root", str(root), "--release-dir", str(new),
                    "--profile", "dgx-spark-arm64", "--confirm", "--allow-unsigned",
                ]), 2)

            self.assertEqual(layout.current.resolve().name, "old")
            self.assertEqual(layout.config.stat().st_mode & 0o777, 0o777)
            self.assertEqual(
                json.loads(layout.transaction.read_text(encoding="utf-8"))["phase"],
                "data_changed",
            )

    def test_persistent_fingerprint_detects_xattr_only_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            target = layout.data / "metadata-test"
            target.write_text("unchanged bytes", encoding="utf-8")
            before = appliance.persistent_state_fingerprint(layout)
            os.setxattr(target, "user.ksi-cctv-test", b"changed")

            self.assertNotEqual(before, appliance.persistent_state_fingerprint(layout))

    def test_recover_requires_data_acknowledgement_after_activation_started(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            backup_public, _ = self.backup_recipient(Path(temp))
            layout = appliance.Layout(root)
            with patch.object(appliance, "validate_hardware", return_value=[]), patch.object(
                appliance, "run_command", side_effect=self.successful_command
            ):
                self.assertEqual(appliance.main([
                    "install", "--root", str(root), "--release-dir", str(old),
                    "--profile", "dgx-spark-arm64", "--site-env", str(Path(temp) / "site.env"),
                    "--backup-recipient-public-key", str(backup_public),
                    "--confirm", "--allow-unsigned",
                ]), 0)
                state = json.loads((layout.etc / "appliance.json").read_text(encoding="utf-8"))
                _, staged = appliance.stage_release(
                    layout, new, "dgx-spark-arm64", None, True
                )
                previous = layout.current.resolve()
                appliance.atomic_switch(layout.current, staged)
                appliance.write_transaction(layout, {
                    "operation": "upgrade",
                    "previous": str(previous),
                    "candidate": str(staged),
                    "installed_state": state,
                    "prior_rollback": None,
                    "service_was_active": True,
                    "service_was_enabled": True,
                }, "activation_started")
                base = [
                    "recover", "--root", str(root), "--profile", "dgx-spark-arm64",
                    "--confirm", "--allow-unsigned",
                ]
                self.assertEqual(appliance.main(base), 2)
                self.assertTrue(layout.transaction.exists())
                self.assertEqual(appliance.main([*base, "--accept-live-data"]), 0)

            self.assertEqual(layout.current.resolve().name, "old")
            self.assertFalse((layout.releases / "new").exists())
            self.assertFalse(layout.transaction.exists())

    def test_recover_clears_pre_unit_install_without_systemd_calls(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            candidate = layout.releases / "candidate"
            appliance.write_transaction(layout, {
                "operation": "install",
                "candidate": str(candidate),
                "installed_state": {},
            }, "staged")

            with patch.object(appliance, "run_command") as run:
                self.assertEqual(appliance.main([
                    "recover", "--root", str(layout.root), "--confirm", "--allow-unsigned",
                ]), 0)
            run.assert_not_called()
            self.assertFalse(layout.transaction.exists())

    def test_recover_restores_pre_restore_directories_outside_operation_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            old = layout.etc.with_name(f".{layout.etc.name}.pre-restore")
            old.mkdir()
            (old / "generation").write_text("old", encoding="utf-8")
            (layout.etc / "generation").write_text("new", encoding="utf-8")
            staging = layout.root / "var" / "tmp" / "ksi-cctv-restore-interrupted"
            staging.mkdir(parents=True)
            (staging / "plaintext").write_text("temporary", encoding="utf-8")
            appliance.write_transaction(layout, {
                "operation": "restore",
                "archive_sha256": "a" * 64,
                "safety_backup": str(layout.backups / "safety.enc"),
                "safety_backup_sha256": "b" * 64,
                "service_managed": False,
                "service_was_active": False,
                "targets": [{"target": str(layout.etc), "had_old": True}],
                "parent_transaction": None,
                "staging": str(staging),
            }, "swapping")

            with patch.object(appliance, "run_command") as run:
                self.assertEqual(appliance.main([
                    "recover", "--root", str(layout.root), "--confirm", "--allow-unsigned",
                ]), 0)
            run.assert_not_called()

            self.assertEqual((layout.etc / "generation").read_text(encoding="utf-8"), "old")
            self.assertFalse(layout.transaction.exists())
            self.assertTrue(layout.operation_lock.is_file())
            self.assertFalse(staging.exists())

    def test_recover_finishes_committed_restore_cleanup_without_mixing_generations(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            appliance.ensure_backup_signing_identity(layout)
            targets = []
            for target in (layout.etc, layout.data, layout.log):
                (target / "generation").write_text("restored", encoding="utf-8")
                old = target.with_name(f".{target.name}.pre-restore")
                if target != layout.etc:
                    old.mkdir()
                    (old / "generation").write_text("old", encoding="utf-8")
                targets.append({"target": str(target), "had_old": True})
            staging = layout.root / "var" / "tmp" / "ksi-cctv-restore-committed"
            staging.mkdir(parents=True)
            appliance.write_transaction(layout, {
                "operation": "restore",
                "archive_sha256": "a" * 64,
                "safety_backup": str(layout.backups / "safety.enc"),
                "safety_backup_sha256": "b" * 64,
                "service_managed": False,
                "service_was_active": False,
                "backup_sequence": 1,
                "targets": targets,
                "parent_transaction": None,
                "staging": str(staging),
            }, "committed")

            with patch.object(appliance, "run_command") as run:
                self.assertEqual(appliance.main([
                    "recover", "--root", str(layout.root), "--confirm", "--allow-unsigned",
                ]), 0)
            run.assert_not_called()

            for target in (layout.etc, layout.data, layout.log):
                self.assertEqual((target / "generation").read_text(encoding="utf-8"), "restored")
                self.assertFalse(target.with_name(f".{target.name}.pre-restore").exists())
            self.assertFalse(layout.transaction.exists())

    def test_precommit_restore_reconciliation_covers_unrecorded_swap_boundaries(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            targets = [
                {"target": str(target), "had_old": True}
                for target in (layout.etc, layout.data, layout.log)
            ]
            partial = layout.etc.with_name(f".{layout.etc.name}.restore-new")
            partial.mkdir()
            (partial / "partial").write_text("candidate", encoding="utf-8")

            data_old = layout.data.with_name(f".{layout.data.name}.pre-restore")
            os.replace(layout.data, data_old)

            log_old = layout.log.with_name(f".{layout.log.name}.pre-restore")
            os.replace(layout.log, log_old)
            layout.log.mkdir()
            (layout.log / "generation").write_text("candidate", encoding="utf-8")
            (log_old / "generation").write_text("old", encoding="utf-8")

            appliance.reconcile_restore_targets(layout, targets, committed=False)

            self.assertFalse(partial.exists())
            self.assertTrue(layout.data.is_dir())
            self.assertFalse(data_old.exists())
            self.assertEqual((layout.log / "generation").read_text(encoding="utf-8"), "old")
            self.assertFalse(log_old.exists())

    def test_recover_cleans_journaled_plaintext_before_any_new_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            staging = layout.root / "var" / "tmp" / "ksi-cctv-backup-decrypt-interrupted"
            appliance.register_sensitive_staging(layout, staging)
            staging.mkdir(parents=True)
            (staging / "backup.tar.gz").write_text("plaintext", encoding="utf-8")

            with patch.object(appliance, "run_command") as run:
                self.assertEqual(appliance.main([
                    "recover", "--root", str(layout.root), "--confirm", "--allow-unsigned",
                ]), 0)
            run.assert_not_called()

            self.assertFalse(staging.exists())
            self.assertFalse(layout.sensitive_staging.exists())

    def test_recover_quiescing_restore_restarts_the_previously_active_service(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            staging = layout.root / "var" / "tmp" / "ksi-cctv-restore-quiescing"
            staging.mkdir(parents=True)
            targets = [
                {"target": str(target), "had_old": True}
                for target in (layout.etc, layout.data, layout.log)
            ]
            appliance.write_transaction(layout, {
                "operation": "restore",
                "archive_sha256": "a" * 64,
                "service_managed": True,
                "service_was_active": True,
                "targets": targets,
                "parent_transaction": None,
                "staging": str(staging),
            }, "quiescing")

            with (
                patch.object(appliance, "run_command"),
                patch.object(appliance, "service_action", side_effect=[0, 0]) as service,
            ):
                self.assertEqual(appliance.main([
                    "recover", "--root", str(layout.root), "--confirm", "--allow-unsigned",
                ]), 0)

            self.assertEqual([call.args[0] for call in service.call_args_list], ["stop", "restart"])
            self.assertFalse(layout.transaction.exists())

    def test_recover_interrupted_backup_restores_prior_active_state(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            appliance.write_transaction(layout, {
                "operation": "backup",
                "service_managed": True,
                "service_was_active": True,
            }, "quiescing")

            with (
                patch.object(appliance, "service_action", side_effect=[0, 0]) as service,
                patch.object(appliance, "verify_active_health") as health,
                patch.object(appliance, "run_command"),
            ):
                self.assertEqual(appliance.main([
                    "recover", "--root", str(layout.root), "--confirm", "--allow-unsigned",
                ]), 0)

            self.assertEqual([call.args[0] for call in service.call_args_list], ["stop", "restart"])
            health.assert_called_once()
            self.assertFalse(layout.transaction.exists())

    def test_inactive_activation_switches_code_without_starting_and_preserves_disablement(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            old_manifest, old_staged = appliance.stage_release(
                layout, old, "dgx-spark-arm64", None, True
            )
            new_manifest, new_staged = appliance.stage_release(
                layout, new, "dgx-spark-arm64", None, True
            )
            appliance.atomic_switch(layout.current, old_staged)
            with (
                patch.object(appliance, "load_images"),
                patch.object(appliance, "service_action") as service,
            ):
                appliance.activate_release(
                    layout,
                    new_staged,
                    new_manifest,
                    5,
                    start_service=False,
                    restore_service_enabled=False,
                )
            service.assert_not_called()
            self.assertEqual(layout.current.resolve(), new_staged)

            with patch.object(
                appliance,
                "run_command",
                side_effect=self.successful_command,
            ) as run:
                appliance.install_systemd_unit(layout, new_staged, enable_service=False)
            self.assertIn(
                ["systemctl", "disable", appliance.SERVICE],
                [call.args[0] for call in run.call_args_list],
            )
            self.assertEqual(old_manifest["version"], "old")

    def test_operation_lock_rejects_a_concurrent_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            with appliance.appliance_operation_lock(layout), patch.object(appliance, "run_command") as run:
                self.assertEqual(appliance.main([
                    "start", "--root", str(layout.root), "--confirm",
                ]), 2)
            run.assert_not_called()

    def test_image_load_failure_restores_previous_signed_systemd_unit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            old, _ = self.make_release(Path(temp), "old")
            new, _ = self.make_release(Path(temp), "new")
            old_unit = old / "systemd" / appliance.SERVICE
            new_unit = new / "systemd" / appliance.SERVICE
            old_unit.write_text(old_unit.read_text(encoding="utf-8") + "# old\n", encoding="utf-8")
            new_unit.write_text(new_unit.read_text(encoding="utf-8") + "# new\n", encoding="utf-8")
            self.declare_file(old, f"systemd/{appliance.SERVICE}")
            self.declare_file(new, f"systemd/{appliance.SERVICE}")
            old_manifest, old_staged = appliance.stage_release(layout, old, "dgx-spark-arm64", None, True)
            new_manifest, new_staged = appliance.stage_release(layout, new, "dgx-spark-arm64", None, True)
            appliance.atomic_switch(layout.current, old_staged)
            installed_unit = root / "etc" / "systemd" / "system" / appliance.SERVICE

            def run(command, timeout=None):
                if command[:4] == ["docker", "image", "load", "--input"]:
                    return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "run_command", side_effect=run):
                appliance.install_systemd_unit(layout, old_staged)
                appliance.install_systemd_unit(layout, new_staged)
                with self.assertRaises(appliance.ApplianceError):
                    appliance.activate_release(
                        layout,
                        new_staged,
                        new_manifest,
                        10,
                        restore_unit_on_failure=True,
                    )

            self.assertEqual(layout.current.resolve(), old_staged)
            self.assertEqual(installed_unit.read_text(encoding="utf-8"), old_unit.read_text(encoding="utf-8"))
            self.assertEqual(old_manifest["version"], "old")

    def test_failed_first_install_stops_stack_and_removes_current_link(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            release, _ = self.make_release(Path(temp), "failed")
            manifest, staged = appliance.stage_release(
                layout, release, "dgx-spark-arm64", None, True
            )
            calls = []

            def commands(command, timeout=None):
                calls.append(command)
                if command == list(appliance.ACTIVATION_HEALTH_COMMAND):
                    return subprocess.CompletedProcess(command, 1, "", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "run_command", side_effect=commands), self.assertRaises(
                appliance.ApplianceError
            ):
                appliance.activate_release(layout, staged, manifest, 10)

            self.assertFalse(layout.current.exists())
            self.assertIn(["systemctl", "stop", appliance.SERVICE], calls)

    def test_activation_loads_declared_images_before_restarting_service(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "root"
            release, _ = self.make_release(Path(temp))
            layout = appliance.Layout(root)
            appliance.prepare_layout(layout)
            staged_manifest, staged = appliance.stage_release(layout, release, "dgx-spark-arm64", None, True)
            calls = []

            def commands(command, timeout=None):
                calls.append(command)
                return self.successful_command(command, timeout)

            with patch.object(appliance, "run_command", side_effect=commands):
                appliance.activate_release(layout, staged, staged_manifest, 10)
            self.assertLess(calls.index(["docker", "image", "load", "--input", str(staged / "images/engine.tar")]), calls.index(["systemctl", "restart", appliance.SERVICE]))
            self.assertIn(
                ["docker", "image", "inspect", "--format", "{{.Id}} {{.Architecture}}", fixture_image_ref("engine")],
                calls,
            )

    def test_activation_rejects_a_non_arm64_loaded_image(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            release, _ = self.make_release(Path(temp))
            manifest, staged = appliance.stage_release(layout, release, "dgx-spark-arm64", None, True)

            def commands(command, timeout=None):
                if command[:4] == ["docker", "image", "inspect", "--format"]:
                    return subprocess.CompletedProcess(command, 0, f"{command[-1]} amd64\n", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "run_command", side_effect=commands), self.assertRaises(appliance.ApplianceError):
                appliance.activate_release(layout, staged, manifest, 10)

    def test_partial_image_load_removes_only_newly_introduced_images(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            manifest = appliance.load_manifest(release)
            calls: list[list[str]] = []
            load_count = 0
            attempted: set[str] = set()

            def commands(command, timeout=None):
                nonlocal load_count
                calls.append(command)
                if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
                    return subprocess.CompletedProcess(command, 0, "/\n", "")
                if command[:3] == ["docker", "image", "inspect"] and "--format" not in command:
                    return subprocess.CompletedProcess(command, 0 if command[-1] in attempted else 1, "", "")
                if command[:4] == ["docker", "image", "load", "--input"]:
                    load_count += 1
                    attempted.add(sorted(manifest["images"])[load_count - 1])
                    return subprocess.CompletedProcess(command, 1 if load_count == 2 else 0, "", "")
                if command[:4] == ["docker", "image", "inspect", "--format"]:
                    return subprocess.CompletedProcess(command, 0, f"{command[-1]} arm64\n", "")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(appliance, "run_command", side_effect=commands), self.assertRaisesRegex(
                appliance.ApplianceError, "failed to load offline image archive"
            ):
                appliance.load_images(release, manifest)

            removals = [command for command in calls if command[:3] == ["docker", "image", "rm"]]
            self.assertEqual(len(removals), 2)

    def test_interrupted_pending_image_is_removed_from_transaction_journal(self):
        image = fixture_image_ref("engine")
        transaction = {
            "operation": "install",
            "phase": "recovery_failed",
            "image_load": {
                "preexisting": [],
                "introduced": [],
                "pending": image,
                "cleanup_failed": [image],
            },
        }
        calls: list[list[str]] = []

        def commands(command, timeout=None):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(appliance, "run_command", side_effect=commands):
            appliance.cleanup_transaction_images(transaction)

        self.assertNotIn("image_load", transaction)
        self.assertIn(["docker", "image", "rm", image], calls)

    def test_failed_image_cleanup_is_retained_in_durable_journal(self):
        with tempfile.TemporaryDirectory() as temp:
            release, _ = self.make_release(Path(temp))
            manifest = appliance.load_manifest(release)
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            appliance.write_transaction(layout, {
                "operation": "install",
                "candidate": str(layout.releases / "candidate"),
                "installed_state": {},
            }, "activation_started")
            attempted: set[str] = set()

            def commands(command, timeout=None):
                if command == ["docker", "info", "--format", "{{.DockerRootDir}}"]:
                    return subprocess.CompletedProcess(command, 0, "/\n", "")
                if command[:3] == ["docker", "image", "inspect"] and "--format" not in command:
                    return subprocess.CompletedProcess(command, 0 if command[-1] in attempted else 1, "", "")
                if command[:4] == ["docker", "image", "load", "--input"]:
                    attempted.add(sorted(manifest["images"])[0])
                    return subprocess.CompletedProcess(command, 1, "", "")
                if command[:3] == ["docker", "image", "rm"]:
                    return subprocess.CompletedProcess(command, 1, "", "cleanup failed")
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch.object(appliance, "run_command", side_effect=commands), self.assertRaises(
                appliance.ImageCleanupUnsafeError
            ):
                appliance.load_images(release, manifest, layout=layout)

            journal = appliance.load_transaction(layout)
            assert journal is not None
            self.assertEqual(journal["image_load"]["cleanup_failed"], sorted(attempted))

    def test_activation_rejects_loaded_image_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            release, _ = self.make_release(Path(temp))
            manifest, staged = appliance.stage_release(
                layout, release, "dgx-spark-arm64", None, True
            )

            def commands(command, timeout=None):
                if command[:4] == ["docker", "image", "inspect", "--format"]:
                    return subprocess.CompletedProcess(command, 0, f"sha256:{'f' * 64} arm64\n", "")
                return self.successful_command(command, timeout)

            with patch.object(appliance, "run_command", side_effect=commands), self.assertRaisesRegex(
                appliance.ApplianceError, "failed first-install stack stopped"
            ) as caught:
                appliance.activate_release(layout, staged, manifest, 10)
            self.assertIn(
                "loaded image identity or architecture is invalid",
                str(caught.exception.__cause__),
            )

    def test_loaded_engine_models_must_match_signed_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            layout = appliance.Layout(Path(temp) / "root")
            appliance.prepare_layout(layout)
            release, _ = self.make_release(Path(temp))
            manifest, staged = appliance.stage_release(
                layout, release, "dgx-spark-arm64", None, True
            )

            def commands(command, timeout=None):
                result = self.successful_command(command, timeout)
                if command[:2] == ["docker", "cp"]:
                    (Path(command[3]) / "test.onnx").write_bytes(b"wrong-model")
                return result

            with patch.object(appliance, "run_command", side_effect=commands), self.assertRaisesRegex(
                appliance.ApplianceError, "do not match the signed model inventory"
            ):
                appliance.load_images(staged, manifest)

    def test_site_environment_is_required_and_copied_owner_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout = appliance.Layout(root / "root")
            appliance.prepare_layout(layout)
            release = root / "release"
            release.mkdir()
            (release / "compose.yaml").write_text(
                self.site_environment_compose(),
                encoding="utf-8",
            )
            with self.assertRaises(appliance.ApplianceError):
                appliance.install_site_environment(layout, release, None)
            source = root / "site.env"
            source.write_text(
                f"KSI_CCTV_API_KEY={API_KEY}\n"
                "KSI_CCTV_SITE_ADDRESS=cctv-appliance.local\n"
                f"KSI_CCTV_WEB_PASSWORD={WEB_PASSWORD}\n"
                f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n"
                f"KSI_CCTV_CAPTURE_KEY={CAPTURE_KEY}\n",
                encoding="utf-8",
            )
            os.chmod(source, 0o600)

            appliance.install_site_environment(layout, release, str(source))

            self.assertEqual(layout.site_environment.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                layout.site_environment.read_text(encoding="utf-8"),
                "KSI_CCTV_SITE_ADDRESS=cctv-appliance.local\n",
            )
            self.assertEqual(
                layout.engine_environment.read_text(encoding="utf-8"),
                f"KSI_CCTV_API_KEY={API_KEY}\nKSI_CCTV_CAPTURE_KEY={CAPTURE_KEY}\n",
            )
            self.assertEqual(
                layout.web_environment.read_text(encoding="utf-8"),
                f"KSI_CCTV_API_KEY={API_KEY}\nKSI_CCTV_WEB_PASSWORD={WEB_PASSWORD}\n"
                f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n",
            )

    def test_site_environment_rejects_invalid_capture_key(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout = appliance.Layout(root / "root")
            appliance.prepare_layout(layout)
            release = root / "release"
            release.mkdir()
            (release / "compose.yaml").write_text(
                self.site_environment_compose(),
                encoding="utf-8",
            )
            source = root / "site.env"
            source.write_text(
                f"KSI_CCTV_API_KEY={API_KEY}\n"
                "KSI_CCTV_SITE_ADDRESS=cctv-appliance.local\n"
                f"KSI_CCTV_WEB_PASSWORD={WEB_PASSWORD}\n"
                f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n"
                "KSI_CCTV_CAPTURE_KEY=not-a-fernet-key\n",
                encoding="utf-8",
            )
            os.chmod(source, 0o600)

            with self.assertRaises(appliance.ApplianceError):
                appliance.install_site_environment(layout, release, str(source))

    def test_site_environment_rejects_address_with_port(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout = appliance.Layout(root / "root")
            appliance.prepare_layout(layout)
            release = root / "release"
            release.mkdir()
            (release / "compose.yaml").write_text(
                self.site_environment_compose(),
                encoding="utf-8",
            )
            source = root / "site.env"
            for address in (
                "cctv-appliance.local:80",
                "0.1.2.3",
                "192.0.2.1",
                "198.18.0.1",
            ):
                with self.subTest(address=address):
                    source.write_text(
                        f"KSI_CCTV_API_KEY={API_KEY}\n"
                        f"KSI_CCTV_SITE_ADDRESS={address}\n"
                        f"KSI_CCTV_WEB_PASSWORD={WEB_PASSWORD}\n"
                        f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n"
                        f"KSI_CCTV_CAPTURE_KEY={CAPTURE_KEY}\n",
                        encoding="utf-8",
                    )
                    os.chmod(source, 0o600)
                    with self.assertRaisesRegex(appliance.ApplianceError, "hostname or usable IPv4"):
                        appliance.install_site_environment(layout, release, str(source))

    def test_site_environment_rejects_development_or_loopback_options(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout = appliance.Layout(root / "root")
            appliance.prepare_layout(layout)
            release = root / "release"
            release.mkdir()
            (release / "compose.yaml").write_text(
                self.site_environment_compose(),
                encoding="utf-8",
            )
            source = root / "site.env"
            source.write_text(
                f"KSI_CCTV_API_KEY={API_KEY}\n"
                "KSI_CCTV_SITE_ADDRESS=cctv-appliance.local\n"
                f"KSI_CCTV_WEB_PASSWORD={WEB_PASSWORD}\n"
                f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n"
                f"KSI_CCTV_CAPTURE_KEY={CAPTURE_KEY}\n"
                "KSI_CCTV_ALLOW_LOOPBACK_CAMERA=true\n",
                encoding="utf-8",
            )
            os.chmod(source, 0o600)

            with self.assertRaises(appliance.ApplianceError):
                appliance.install_site_environment(layout, release, str(source))

    def test_site_environment_rejects_world_readable_source(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout = appliance.Layout(root / "root")
            appliance.prepare_layout(layout)
            release = root / "release"
            release.mkdir()
            (release / "compose.yaml").write_text(
                self.site_environment_compose(),
                encoding="utf-8",
            )
            source = root / "site.env"
            source.write_text(
                f"KSI_CCTV_API_KEY={API_KEY}\n"
                "KSI_CCTV_SITE_ADDRESS=cctv-appliance.local\n"
                f"KSI_CCTV_WEB_PASSWORD={WEB_PASSWORD}\n"
                f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n"
                f"KSI_CCTV_CAPTURE_KEY={CAPTURE_KEY}\n",
                encoding="utf-8",
            )
            os.chmod(source, 0o644)

            with self.assertRaises(appliance.ApplianceError):
                appliance.install_site_environment(layout, release, str(source))

    def test_site_environment_rejects_compose_metacharacters_in_password(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            layout = appliance.Layout(root / "root")
            appliance.prepare_layout(layout)
            release = root / "release"
            release.mkdir()
            (release / "compose.yaml").write_text(
                self.site_environment_compose(),
                encoding="utf-8",
            )
            source = root / "site.env"
            for password in ("Unsafe$Password123", "Unsafe Password123", "Unsafe#Password123"):
                with self.subTest(password=password):
                    source.write_text(
                        f"KSI_CCTV_API_KEY={API_KEY}\n"
                        "KSI_CCTV_SITE_ADDRESS=cctv-appliance.local\n"
                        f"KSI_CCTV_WEB_PASSWORD={password}\n"
                        f"KSI_CCTV_WEB_SECRET={WEB_SECRET}\n"
                        f"KSI_CCTV_CAPTURE_KEY={CAPTURE_KEY}\n",
                        encoding="utf-8",
                    )
                    os.chmod(source, 0o600)
                    with self.assertRaisesRegex(appliance.ApplianceError, "Compose-safe"):
                        appliance.install_site_environment(layout, release, str(source))
