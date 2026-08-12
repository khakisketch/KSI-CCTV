#!/usr/bin/env python3
"""Block Appliance service boot while durable recovery state is unresolved."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

TRANSACTION = Path("/opt/ksi-cctv/transaction.json")
SENSITIVE_STAGING = Path("/opt/ksi-cctv/sensitive-staging.json")
AUTHORIZATION = Path("/run/ksi-cctv/ksi-cctv-transaction-start-authorized")


def start_allowed(
    transaction: Path = TRANSACTION,
    sensitive_staging: Path = SENSITIVE_STAGING,
    authorization: Path = AUTHORIZATION,
    expected_uid: int = 0,
    proc_root: Path = Path("/proc"),
) -> bool:
    def present(path: Path) -> bool:
        try:
            path.lstat()
        except FileNotFoundError:
            return False
        return True

    def unlink_durable(path: Path) -> bool:
        try:
            path.unlink()
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return True

    try:
        recovery_pending = present(transaction) or present(sensitive_staging)
    except OSError:
        return False
    if not recovery_pending:
        return unlink_durable(authorization)
    claimed = authorization.with_name(f".{authorization.name}.claimed-{os.getpid()}")
    try:
        os.rename(authorization, claimed)
        directory_fd = os.open(authorization.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError:
        return False
    fd: int | None = None
    valid = False
    try:
        fd = os.open(claimed, os.O_RDONLY | os.O_NOFOLLOW)
        metadata = os.fstat(fd)
        content = os.read(fd, 4097)
        value = json.loads(content.decode("ascii"))
        pid = value.get("pid") if isinstance(value, dict) and set(value) == {"pid", "start_time"} else None
        start_time = value.get("start_time") if isinstance(value, dict) else None
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or not isinstance(pid, int)
            or pid <= 0
            or not isinstance(start_time, str)
            or not start_time
        ):
            return False
        status = (proc_root / str(pid) / "status").read_text(encoding="ascii")
        uid_line = next((line for line in status.splitlines() if line.startswith("Uid:")), "")
        if not uid_line or int(uid_line.split()[1]) != expected_uid:
            return False
        process_stat = (proc_root / str(pid) / "stat").read_text(encoding="ascii")
        process_fields = process_stat.rsplit(")", 1)[1].split()
        process_state = process_fields[0]
        actual_start_time = process_fields[19]
        valid = process_state not in {"Z", "X", "x"} and actual_start_time == start_time
    except (OSError, UnicodeError, ValueError, IndexError, json.JSONDecodeError):
        valid = False
    finally:
        if fd is not None:
            os.close(fd)
    consumed = unlink_durable(claimed)
    return valid and consumed


def main() -> int:
    if start_allowed():
        return 0
    print("KSI-CCTV recovery state blocks automatic service start", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
