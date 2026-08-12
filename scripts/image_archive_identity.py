#!/usr/bin/env python3
"""Print the verified Linux ARM64 config identity from one tagless Docker save."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "installer"))

from release_contracts import ReleaseContractError, validate_single_image_archive  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and identify one offline ARM64 image archive")
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--expect")
    args = parser.parse_args(argv)
    try:
        identity = validate_single_image_archive(args.archive, args.expect)
    except ReleaseContractError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(identity)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
