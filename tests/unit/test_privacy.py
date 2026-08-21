"""Unit tests for privacy guardrails."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import get_type_hints

import pytest

from mootloop import privacy, secrets
from mootloop.errors import OutboundPrivacyError
from mootloop.models.common import PublicText
from mootloop.privacy import (
    CANARY_REGISTRY_ENV,
    _default_registry,
    load_registry,
    privacy_grep,
    seed_canary,
    serialize_outbound,
)


def test_canary_registry_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hosted tier's read-only ~/.mootloop is bypassed via the env override:
    seeding with no explicit registry_path must write to the env-pointed path."""
    writable = tmp_path / "matters-root" / ".canaries.json"
    monkeypatch.setenv(CANARY_REGISTRY_ENV, str(writable))
    assert _default_registry() == writable

    vault = tmp_path / "vault"
    vault.mkdir()
    token = seed_canary(vault, "hosted-matter")  # no registry_path -> env default

    assert writable.is_file()
    assert load_registry(writable)["canaries"][token] == "hosted-matter"


def test_canary_registry_default_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset env keeps the historical ~/.mootloop/canaries.json default (local dev)."""
    monkeypatch.delenv(CANARY_REGISTRY_ENV, raising=False)
    assert _default_registry() == Path.home() / ".mootloop" / "canaries.json"


def test_outbound_canary_blocks_before_json_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = tmp_path / "canaries.json"
    registry.write_text(
        json.dumps({"canaries": {"MOOTLOOP-CANARY-sibling": "sibling"}, "denylist": []}),
        encoding="utf-8",
    )
    serialized = False

    def should_not_serialize(*args: object, **kwargs: object) -> str:
        nonlocal serialized
        serialized = True
        return "{}"

    monkeypatch.setattr("mootloop.privacy.json.dumps", should_not_serialize)
    with pytest.raises(OutboundPrivacyError, match="canary"):
        serialize_outbound({"event": "MOOTLOOP-CANARY-sibling"}, registry_path=registry)
    assert serialized is False


def test_outbound_exact_secret_blocks_before_json_serialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("CUSTOM_SINK_TOKEN=plain-exact-value\n", encoding="utf-8")
    serialized = False

    def should_not_serialize(*args: object, **kwargs: object) -> str:
        nonlocal serialized
        serialized = True
        return "{}"

    monkeypatch.setattr("mootloop.privacy.json.dumps", should_not_serialize)
    with pytest.raises(OutboundPrivacyError, match="secret"):
        serialize_outbound({"event": "contains plain-exact-value"}, secrets_file=secrets_file)
    assert serialized is False


def test_outbound_registered_exact_secret_blocks(tmp_path: Path) -> None:
    secrets.register_secret("registered-custom-literal-u02")
    with pytest.raises(OutboundPrivacyError, match="secret"):
        serialize_outbound(
            {"event": "registered-custom-literal-u02"},
            registry_path=tmp_path / "missing-canaries.json",
            secrets_file=tmp_path / "missing-secrets.env",
        )


def test_allowed_outbound_payload_is_redacted_and_typed(tmp_path: Path) -> None:
    payload = serialize_outbound(
        {"message": "authorization Bearer shaped-token"},
        registry_path=tmp_path / "missing-canaries.json",
        secrets_file=tmp_path / "missing-secrets.env",
    )
    assert payload == '{"message":"authorization ***REDACTED***"}'
    assert get_type_hints(serialize_outbound)["return"] is PublicText


def test_outbound_policy_sources_are_loaded_once_per_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loads = {"registry": 0, "secrets": 0}

    def load_registry_once(path: Path | str | None = None) -> dict[str, object]:
        loads["registry"] += 1
        return {"canaries": {}, "denylist": []}

    def build_matcher_once(*, secrets_file: Path) -> object:
        loads["secrets"] += 1
        return lambda text: False

    monkeypatch.setattr(privacy, "load_registry", load_registry_once)
    monkeypatch.setattr(privacy.secret_store, "exact_secret_matcher", build_matcher_once)

    serialize_outbound(
        {"outer": [{"inner": "one"}, {"inner": "two"}]},
        registry_path=tmp_path / "registry.json",
        secrets_file=tmp_path / "secrets.env",
    )

    assert loads == {"registry": 1, "secrets": 1}


def test_outbound_payload_rejects_non_string_mapping_keys(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="mapping keys"):
        serialize_outbound(
            {1: "value"},
            registry_path=tmp_path / "missing-canaries.json",
            secrets_file=tmp_path / "missing-secrets.env",
        )


@pytest.mark.parametrize(
    "registry_kind",
    ["missing", "directory", "symlink", "malformed", "bad-canaries", "bad-denylist"],
)
def test_hosted_outbound_policy_requires_strict_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_kind: str,
) -> None:
    registry = tmp_path / "canaries.json"
    if registry_kind == "directory":
        registry.mkdir()
    elif registry_kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text('{"canaries":{},"denylist":[]}', encoding="utf-8")
        registry.symlink_to(target)
    elif registry_kind == "malformed":
        registry.write_text("{", encoding="utf-8")
    elif registry_kind == "bad-canaries":
        registry.write_text('{"canaries":[],"denylist":[]}', encoding="utf-8")
    elif registry_kind == "bad-denylist":
        registry.write_text('{"canaries":{},"denylist":{}}', encoding="utf-8")
    monkeypatch.setenv("MOOTLOOP_RUNTIME_MODE", "hosted")

    with pytest.raises(OutboundPrivacyError, match="hosted"):
        serialize_outbound(
            {"event": "safe"},
            registry_path=registry,
            secrets_file=tmp_path / "missing-secrets.env",
        )


def test_local_outbound_policy_keeps_missing_registry_compatibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOOTLOOP_RUNTIME_MODE", "local")
    assert (
        serialize_outbound(
            {"event": "safe"},
            registry_path=tmp_path / "missing-canaries.json",
            secrets_file=tmp_path / "missing-secrets.env",
        )
        == '{"event":"safe"}'
    )


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


def test_internal_symlink_to_untracked_target_is_scanned(tmp_path: Path) -> None:
    """A tracked symlink pointing at an UNTRACKED file inside the repo is a hole.

    Nothing else scans that content — there is no tracked entry for it — so skipping
    the link on "the target is scanned on its own" leaked a canary past the grep.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    registry = tmp_path / "canaries.json"
    vault = tmp_path / "vault"
    vault.mkdir()
    token = seed_canary(vault, "leaky-matter", registry_path=registry)

    # An untracked (gitignored) scratch file inside the repo, holding matter text.
    (repo / "scratch.txt").write_text(f"pasted {token} while debugging")
    (repo / ".gitignore").write_text("scratch.txt\n")
    (repo / "notes.md").symlink_to("scratch.txt")
    _git_add(repo, ".gitignore", "notes.md")

    findings = privacy_grep(repo, registry_path=registry)
    assert any(f.kind == "canary" and f.path == "notes.md" for f in findings)


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


@pytest.mark.parametrize(
    "filename",
    [
        "naïve.txt",  # accented latin — routine in a legal corpus
        "Müller Decl.txt",
        "smart’quote.txt",
        "訴状.txt",  # CJK
    ],
)
def test_canary_is_found_in_a_file_git_would_quote(tmp_path: Path, filename: str) -> None:
    """`git ls-files` C-quotes non-ASCII paths under the default `core.quotePath`, and
    the quoted literal names no real file — so these leaks were skipped as if they were
    staged deletions. Fail-open in the only pre-commit leak blocker."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    registry = tmp_path / "canaries.json"
    vault = tmp_path / "vault"
    vault.mkdir()
    token = seed_canary(vault, "leaky-matter", registry_path=registry)
    (repo / filename).write_text(f"privileged excerpt {token}", encoding="utf-8")
    _git_add(repo, "-A")

    findings = privacy_grep(repo, registry_path=registry)
    assert any(f.kind == "canary" and f.path == filename for f in findings), findings


def test_staged_non_ascii_path_is_scanned_too(tmp_path: Path) -> None:
    """The staged list (`diff --cached --name-only`) quotes identically."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    registry = tmp_path / "canaries.json"
    registry.write_text('{"canaries": {}, "denylist": ["SuperSecretParty"]}')
    (repo / "Peña Decl.txt").write_text("re: SuperSecretParty v. Others", encoding="utf-8")
    _git_add(repo, "-A")

    findings = privacy_grep(repo, registry_path=registry)
    assert any(f.kind == "denylist" for f in findings), findings


def test_unstattable_entry_is_unscannable_not_skipped(tmp_path: Path) -> None:
    """An entry the process cannot stat is a finding, not a silent pass — `exists()`
    swallows every OSError, so an unsearchable parent read as "staged deletion"."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo)
    sub = repo / "sealed"
    sub.mkdir()
    (sub / "exhibit.txt").write_text("privileged", encoding="utf-8")
    _git_add(repo, "-A")
    sub.chmod(0o000)
    try:
        findings = privacy_grep(repo, registry_path=tmp_path / "empty.json")
    finally:
        sub.chmod(0o755)
    assert any(f.kind == "unscannable" for f in findings), findings
