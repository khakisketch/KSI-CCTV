from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

INSTALLER = Path(__file__).parents[1] / "installer"
sys.path.insert(0, str(INSTALLER))
import release_contracts as contracts

from tests.release_contract_fixture import (
    PERSISTENT_STATE,
    add_release_contract,
    image_ref,
    refresh_bundle_attestation,
    write_test_image_archive,
)

IMAGE_ARCHIVES = {
    image_ref(name): f"images/{name}.tar"
    for name in ("engine", "web", "mediamtx", "caddy")
}
IMAGES = set(IMAGE_ARCHIVES)
ENGINE_IMAGE = image_ref("engine")
WEB_IMAGE = image_ref("web")


def contract_fixture(root: Path) -> tuple[Path, Path, dict[str, object], set[str]]:
    release = root / "release"
    release.mkdir()
    for image, archive in IMAGE_ARCHIVES.items():
        path = release / archive
        path.parent.mkdir(exist_ok=True)
        write_test_image_archive(path, image)
    public_key = add_release_contract(release, IMAGE_ARCHIVES, root)
    contract = contracts.load_release_contract(release)
    files = {
        path.relative_to(release).as_posix(): contracts.hashlib.sha256(path.read_bytes()).hexdigest()
        for path in release.rglob("*") if path.is_file()
    }
    return release, public_key, contract, files


def test_valid_release_contract_and_external_attestation_key_are_accepted() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))

        assert contracts.validate_release_contract(
            release, contract, IMAGES, files, version="v1",
            profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
            engine_image=ENGINE_IMAGE,
            trusted_attestation_key=public_key,
        ) == contract


def test_release_contract_rejects_tampered_gate_evidence() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        (release / "evidence" / "pytest-summary.json").write_text("tampered\n", encoding="utf-8")

        with pytest.raises(
            contracts.ReleaseContractError,
            match="evidence digest mismatch|pytest summary is invalid",
        ):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_ci_bundle_attestation_cannot_be_replayed_for_changed_payload() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        archive = release / IMAGE_ARCHIVES[next(iter(sorted(IMAGES)))]
        archive.write_bytes(b"replacement image signed only by the release key")
        files[archive.relative_to(release).as_posix()] = hashlib.sha256(archive.read_bytes()).hexdigest()

        with pytest.raises(
            contracts.ReleaseContractError,
            match="exact release payload|archive inventory scan failed closed",
        ):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_ci_bundle_attestation_binds_raw_contract_bytes() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, _contract, files = contract_fixture(Path(temp))
        contract_path = release / contracts.CONTRACT_PATH
        contract_path.write_bytes(contract_path.read_bytes() + b" ")
        files[contracts.CONTRACT_PATH] = hashlib.sha256(contract_path.read_bytes()).hexdigest()
        contract = contracts.load_release_contract(release)

        with pytest.raises(contracts.ReleaseContractError, match="exact release payload"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_json_rejects_duplicate_keys() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, _public_key, _contract, _files = contract_fixture(Path(temp))
        contract_path = release / contracts.CONTRACT_PATH
        raw = contract_path.read_text(encoding="utf-8").strip()
        contract_path.write_text('{"source":{},' + raw[1:], encoding="utf-8")

        with pytest.raises(contracts.ReleaseContractError, match="duplicate key"):
            contracts.load_release_contract(release)


def test_release_contract_rejects_unapproved_payload_path() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        extra = release / "customer-backup.zip"
        extra.write_bytes(b"not an approved release artifact")
        files[extra.name] = hashlib.sha256(extra.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="unapproved path"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_modified_allowed_default() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        release, public_key, contract, files = contract_fixture(root)
        default_path = release / "defaults" / "config" / "default.yaml"
        default_path.parent.mkdir(parents=True, exist_ok=True)
        default_path.write_text("site_name: customer-specific\n", encoding="utf-8")
        refresh_bundle_attestation(release, IMAGE_ARCHIVES, root, version="v1")
        files = {
            path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in release.rglob("*") if path.is_file()
        }

        with pytest.raises(contracts.ReleaseContractError, match="clean gated source"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_requires_complete_runtime_defaults() -> None:
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        release, public_key, contract, _files = contract_fixture(root)
        (release / "defaults" / "config" / "event_policies.yaml").unlink()
        refresh_bundle_attestation(release, IMAGE_ARCHIVES, root, version="v1")
        files = {
            path.relative_to(release).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in release.rglob("*") if path.is_file()
        }

        with pytest.raises(contracts.ReleaseContractError, match="missing a required runtime default"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_partial_gate_attestation_schema() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        path = release / "evidence" / "release-attestation.json"
        attestation = json.loads(path.read_text(encoding="utf-8"))
        del attestation["tracked_tree_sha256"]
        path.write_bytes(contracts.canonical_json(attestation) + b"\n")

        with pytest.raises(contracts.ReleaseContractError, match="schema is incomplete"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_empty_or_unbound_sbom() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        sbom_path = release / next(iter(contract["sboms"].values()))
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        sbom["packages"] = []
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        files[sbom_path.relative_to(release).as_posix()] = hashlib.sha256(sbom_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="describe at least one package"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )

        sbom = json.loads((release / next(iter(contract["sboms"].values()))).read_text(encoding="utf-8"))
        sbom["packages"].append({"SPDXID": "SPDXRef-Unlicensed", "name": "bad"})
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        files[sbom_path.relative_to(release).as_posix()] = hashlib.sha256(sbom_path.read_bytes()).hexdigest()
        with pytest.raises(contracts.ReleaseContractError, match="package identity is invalid"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_model_outside_engine_and_unmatched_approval() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        inventory_path = release / "model-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["models"][0]["image"] = WEB_IMAGE
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        files["model-inventory.json"] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="not the engine image"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )

        inventory["models"][0]["image"] = ENGINE_IMAGE
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        files["model-inventory.json"] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
        approvals_path = release / "license-approvals.json"
        approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
        approvals["models"]["test-general-model"]["status"] = "pending"
        approvals_path.write_text(json.dumps(approvals), encoding="utf-8")
        files["license-approvals.json"] = hashlib.sha256(approvals_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="does not match"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_invalid_spdx_license_expression() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        inventory_path = release / "model-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["models"][0]["code_license"] = "MIT OR"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        files["model-inventory.json"] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="code_license is unapproved"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_duplicate_model_paths() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        inventory_path = release / "model-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        duplicate = dict(inventory["models"][0])
        duplicate["id"] = "duplicate-model-id"
        inventory["models"].append(duplicate)
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        files["model-inventory.json"] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="path must be unique"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_noncanonical_model_paths() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        inventory_path = release / "model-inventory.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["models"][0]["path"] = "/app/models/./general.onnx"
        inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
        files["model-inventory.json"] = hashlib.sha256(inventory_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="path must be unique"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


@pytest.mark.parametrize("mutation", ["duplicate-package", "unknown-description"])
def test_release_contract_rejects_invalid_spdx_package_references(mutation: str) -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        sbom_relative = next(iter(contract["sboms"].values()))
        sbom_path = release / sbom_relative
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        if mutation == "duplicate-package":
            sbom["packages"].append(dict(sbom["packages"][0]))
        else:
            sbom["documentDescribes"] = ["SPDXRef-Unknown-Package"]
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        files[sbom_relative] = hashlib.sha256(sbom_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="SBOM"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_document_package_spdx_id_collision() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        sbom_relative = next(iter(contract["sboms"].values()))
        sbom_path = release / sbom_relative
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        sbom["packages"][0]["SPDXID"] = "SPDXRef-DOCUMENT"
        sbom["documentDescribes"] = ["SPDXRef-DOCUMENT"]
        sbom_path.write_text(json.dumps(sbom), encoding="utf-8")
        files[sbom_relative] = hashlib.sha256(sbom_path.read_bytes()).hexdigest()

        with pytest.raises(contracts.ReleaseContractError, match="package identity"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_noncanonical_artifact_paths() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        contract["model_inventory"] = "./model-inventory.json"

        with pytest.raises(contracts.ReleaseContractError, match="canonical locations"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_colliding_semantic_artifact_roles() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        contract["third_party_notices"] = contract["model_inventory"]

        with pytest.raises(contracts.ReleaseContractError, match="canonical locations"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_evidence_role_reusing_runtime_payload() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        contract["third_party_notices"] = "compose.yaml"

        with pytest.raises(contracts.ReleaseContractError, match="canonical locations"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_noncanonical_source_evidence_location() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        contract["source"]["bundle_attestation"] = "other/bundle-attestation.json"

        with pytest.raises(contracts.ReleaseContractError, match="canonical locations"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_shared_image_archives() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        archives = dict(IMAGE_ARCHIVES)
        first, second = sorted(archives)[:2]
        archives[second] = archives[first]

        with pytest.raises(contracts.ReleaseContractError, match="distinct offline archive"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=archives,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=public_key,
            )


def test_release_contract_rejects_attestation_key_inside_bundle() -> None:
    with tempfile.TemporaryDirectory() as temp:
        release, public_key, contract, files = contract_fixture(Path(temp))
        bundled_key = release / "ci-attestation.pub"
        bundled_key.write_bytes(public_key.read_bytes())

        with pytest.raises(contracts.ReleaseContractError, match="must be outside"):
            contracts.validate_release_contract(
                release, contract, IMAGES, files, version="v1",
                profiles=["dgx-spark-arm64"], image_archives=IMAGE_ARCHIVES,
                engine_image=ENGINE_IMAGE,
                trusted_attestation_key=bundled_key,
            )


def test_persistent_state_contract_rejects_unreadable_rollback_schema() -> None:
    incompatible = json.loads(json.dumps(PERSISTENT_STATE))
    incompatible["stores"]["events"]["rollback_readable_versions"] = [1]

    assert contracts.persistent_contract_compatible(PERSISTENT_STATE, incompatible) is False
    with pytest.raises(contracts.ReleaseContractError, match="rollback readers"):
        contracts.validate_persistent_state(incompatible)
