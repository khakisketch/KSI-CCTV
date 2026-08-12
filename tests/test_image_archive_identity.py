from __future__ import annotations

import importlib.util
from pathlib import Path

from tests.release_contract_fixture import image_ref, write_test_image_archive

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "image_archive_identity",
    ROOT / "scripts" / "image_archive_identity.py",
)
assert SPEC and SPEC.loader
image_archive_identity = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(image_archive_identity)


def test_image_archive_identity_reports_verified_config_id(tmp_path: Path, capsys) -> None:
    archive = tmp_path / "engine.tar"
    expected = image_ref("engine")
    write_test_image_archive(archive, expected)

    assert image_archive_identity.main(["--archive", str(archive), "--expect", expected]) == 0
    assert capsys.readouterr().out == f"{expected}\n"
