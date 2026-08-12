from __future__ import annotations

import json
import os
from pathlib import Path

from installer.transaction_guard import start_allowed


def test_transaction_guard_allows_only_clean_state_or_owner_authorization(tmp_path: Path) -> None:
    transaction = tmp_path / "transaction.json"
    sensitive = tmp_path / "sensitive-staging.json"
    authorization = tmp_path / "authorization"

    authorization.write_text("stale", encoding="ascii")
    assert start_allowed(transaction, sensitive, authorization, expected_uid=os.getuid()) is True
    assert not authorization.exists()
    transaction.write_text("{}", encoding="utf-8")
    assert start_allowed(transaction, sensitive, authorization, expected_uid=os.getuid()) is False

    start_time = (Path("/proc") / str(os.getpid()) / "stat").read_text(encoding="ascii").rsplit(")", 1)[1].split()[19]
    authorization.write_text(
        json.dumps({"pid": os.getpid(), "start_time": start_time}), encoding="ascii"
    )
    os.chmod(authorization, 0o600)
    assert start_allowed(transaction, sensitive, authorization, expected_uid=os.getuid()) is True
    assert not authorization.exists()
    assert start_allowed(transaction, sensitive, authorization, expected_uid=os.getuid()) is False

    authorization.write_text(
        json.dumps({"pid": os.getpid(), "start_time": start_time}), encoding="ascii"
    )
    os.chmod(authorization, 0o644)
    assert start_allowed(transaction, sensitive, authorization, expected_uid=os.getuid()) is False


def test_transaction_guard_blocks_sensitive_staging_and_symlink_authorization(tmp_path: Path) -> None:
    transaction = tmp_path / "transaction.json"
    sensitive = tmp_path / "sensitive-staging.json"
    authorization = tmp_path / "authorization"
    target = tmp_path / "target"
    target.write_text(json.dumps({"pid": os.getpid(), "start_time": "invalid"}), encoding="ascii")
    os.chmod(target, 0o600)
    authorization.symlink_to(target)
    sensitive.write_text("{}", encoding="utf-8")

    assert start_allowed(transaction, sensitive, authorization, expected_uid=os.getuid()) is False


def test_transaction_guard_rejects_and_consumes_a_zombie_process_token(tmp_path: Path) -> None:
    transaction = tmp_path / "transaction.json"
    sensitive = tmp_path / "sensitive-staging.json"
    authorization = tmp_path / "authorization"
    proc_root = tmp_path / "proc"
    pid = 4242
    start_time = "123456"
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    (process / "status").write_text(f"Uid:\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\t{os.getuid()}\n", encoding="ascii")
    fields = ["Z", *(["0"] * 18), start_time]
    (process / "stat").write_text(f"{pid} (dead worker) {' '.join(fields)}\n", encoding="ascii")
    transaction.write_text("{}", encoding="utf-8")
    authorization.write_text(json.dumps({"pid": pid, "start_time": start_time}), encoding="ascii")
    os.chmod(authorization, 0o600)

    assert start_allowed(
        transaction,
        sensitive,
        authorization,
        expected_uid=os.getuid(),
        proc_root=proc_root,
    ) is False
    assert not authorization.exists()
