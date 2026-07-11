"""Unit tests for privacy guardrails."""

from __future__ import annotations

import subprocess
from pathlib import Path

from mootloop.privacy import privacy_grep, seed_canary


def _git_init(path: Path) -> None:
    subprocess.run(["git", "-C", str(path), "init", "-q"], check=True)


def _git_add(path: Path, *files: str) -> None:
    subprocess.run(["git", "-C", str(path), "add", *files], check=True)


def test_seeded_canary_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    registry = tmp_path / "canaries.json"
    # Seed a canary into a vault, then plant that token into a repo file.
    vault = tmp_path / "vault"
    vault.mkdir()
    token = seed_canary(vault, "leaky-matter", registry_path=registry)
    (repo / "notes.txt").write_text(f"oops pasted {token} here")
    _git_add(repo, "notes.txt")

    findings = privacy_grep(repo, registry_path=registry)
    kinds = {f.kind for f in findings}
    assert "canary" in kinds


def test_symlink_fails_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    target = tmp_path / "target.txt"
    target.write_text("hi")
    link = repo / "link.txt"
    link.symlink_to(target)
    _git_add(repo, "link.txt")

    findings = privacy_grep(repo, registry_path=tmp_path / "empty.json")
    assert any(f.kind == "unscannable" and f.path == "link.txt" for f in findings)


def test_internal_symlink_to_tracked_file_is_safe(tmp_path: Path) -> None:
    # A symlink resolving to a regular file inside the repo (e.g. CLAUDE.md ->
    # AGENTS.md) is safe: the target is scanned on its own entry.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "AGENTS.md").write_text("nothing sensitive")
    (repo / "CLAUDE.md").symlink_to("AGENTS.md")
    _git_add(repo, "AGENTS.md", "CLAUDE.md")

    findings = privacy_grep(repo, registry_path=tmp_path / "empty.json")
    assert findings == []


def test_binary_file_unscannable(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "blob.bin").write_bytes(b"\xff\xfe\x00\x01\x80binary\xff")
    _git_add(repo, "blob.bin")

    findings = privacy_grep(repo, registry_path=tmp_path / "empty.json")
    assert any(f.kind == "unscannable" and f.path == "blob.bin" for f in findings)


def test_denylist_string_detected(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    registry = tmp_path / "canaries.json"
    registry.write_text('{"canaries": {}, "denylist": ["SuperSecretParty"]}')
    (repo / "doc.txt").write_text("re: SuperSecretParty v. Others")
    _git_add(repo, "doc.txt")

    findings = privacy_grep(repo, registry_path=registry)
    assert any(f.kind == "denylist" for f in findings)


def test_clean_repo_no_findings(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    (repo / "readme.txt").write_text("nothing sensitive here")
    _git_add(repo, "readme.txt")

    findings = privacy_grep(repo, registry_path=tmp_path / "empty.json")
    assert findings == []
