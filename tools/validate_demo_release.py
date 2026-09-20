"""Validate a complete public directory, or stage its pinned archive outside Git."""

from __future__ import annotations

import argparse
from pathlib import Path

from mootloop.web.release import stage_archive, validate_collection


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release", type=Path, help="release directory or uncompressed release.tar")
    parser.add_argument(
        "--pin", type=Path, help="committed release-pin JSON, required for archives"
    )
    parser.add_argument("--stage", type=Path, help="external public root, required for archives")
    args = parser.parse_args()
    if args.pin or args.stage:
        if not args.pin or not args.stage:
            parser.error("--pin and --stage are required together")
        catalog = stage_archive(args.release, args.pin, args.stage)
    else:
        catalog = validate_collection(args.release)
    print(
        f"{catalog.release_id}: {len(catalog.entries)} reviewed demos; "
        "artifacts and local replay inputs validated"
    )


if __name__ == "__main__":
    main()
