#!/usr/bin/env python3
"""Thin entry point for the fail-closed privacy grep.

Scans git-tracked + staged files for registered canary tokens and denylist
strings. Exits non-zero on any finding (including anything unscannable).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running as a bare script (pre-commit) without an installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mootloop.privacy import privacy_grep  # noqa: E402


def main() -> int:
    repo_root = Path.cwd()
    findings = privacy_grep(repo_root)
    if not findings:
        return 0
    print("privacy-grep FAILED — potential leak(s):", file=sys.stderr)
    for f in findings:
        print(f"  [{f.kind}] {f.path}: {f.detail}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
