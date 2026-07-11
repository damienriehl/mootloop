"""Repo-hygiene invariants. These guard the tree against leaked matter data and
structural drift. Run in a dedicated CI job."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from mootloop.privacy import privacy_grep, seed_canary

pytestmark = pytest.mark.invariant

REPO_ROOT = Path(__file__).resolve().parents[2]

# A real canary token is PREFIX + matter_id + "-" + 32 hex chars. The bare prefix
# appears legitimately in source, so match the full token shape only.
CANARY_TOKEN_RE = re.compile(r"MOOTLOOP-CANARY-[a-z0-9._-]+-[0-9a-f]{32}")

# Small denylist fixture — sentinel strings that must never appear in fixtures/.
DENYLIST_FIXTURE = ["ACME-SECRET-PARTY-NAME", "0000-CONFIDENTIAL-0000"]


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [p for p in out if p]


def test_no_canary_token_in_repo() -> None:
    for rel in _tracked_files():
        full = REPO_ROOT / rel
        if full.is_symlink() or not full.is_file():
            continue
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        assert not CANARY_TOKEN_RE.search(text), f"canary token found in {rel}"


def test_privacy_grep_clean_against_seeded_registry(tmp_path: Path) -> None:
    # Seed a canary into a vault OUTSIDE the repo; the token must not appear in
    # the repo, so a grep of the repo with that registry finds no canary.
    vault = tmp_path / "vault"
    vault.mkdir()
    registry = tmp_path / "canaries.json"
    seed_canary(vault, "invariant-matter", registry_path=registry)
    findings = privacy_grep(REPO_ROOT, registry_path=registry)
    canary_hits = [f for f in findings if f.kind == "canary"]
    assert not canary_hits, canary_hits


def test_fixtures_have_no_denylisted_strings() -> None:
    fixtures = REPO_ROOT / "fixtures"
    if not fixtures.exists():
        pytest.skip("no fixtures/ yet")
    for path in fixtures.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for needle in DENYLIST_FIXTURE:
            assert needle not in text, f"denylisted string {needle!r} in {path}"


def test_license_is_mit() -> None:
    license_text = (REPO_ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "MIT License" in license_text


def test_claude_md_symlinks_agents_md() -> None:
    claude = REPO_ROOT / "CLAUDE.md"
    assert claude.is_symlink(), "CLAUDE.md must be a symlink"
    assert os.readlink(claude) == "AGENTS.md"
