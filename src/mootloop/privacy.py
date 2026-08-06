"""Privacy guardrails: per-matter canary tokens and a fail-closed privacy grep.

Canary tokens are seeded into each vault and registered centrally so the repo grep
detects a *known* leak (not guessed PII). The grep fails closed: anything it cannot
read — unreadable file, symlink, or binary — is itself a finding.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mootloop.vault import CANARY_FILE, safe_vault_path

CANARY_PREFIX = "MOOTLOOP-CANARY-"
DEFAULT_REGISTRY = Path.home() / ".mootloop" / "canaries.json"
CANARY_REGISTRY_ENV = "MOOTLOOP_CANARY_REGISTRY"


def _default_registry() -> Path:
    """Resolve the canary registry path.

    Honors the ``MOOTLOOP_CANARY_REGISTRY`` env override so the hosted matter tier —
    whose ``~/.mootloop`` is a *read-only* mount — can point the registry at a writable
    location (e.g. under the matters-root). Local dev, with the var unset, keeps the
    historical ``~/.mootloop/canaries.json`` default.
    """
    override = os.environ.get(CANARY_REGISTRY_ENV)
    return Path(override) if override else DEFAULT_REGISTRY

FindingKind = str  # "canary" | "denylist" | "unscannable"


@dataclass(frozen=True)
class Finding:
    """A privacy-grep hit. Any Finding is a failure."""

    path: str
    kind: FindingKind
    detail: str


# --- registry ---------------------------------------------------------------


def _empty_registry() -> dict[str, Any]:
    return {"canaries": {}, "denylist": []}


def load_registry(registry_path: Path | str | None = None) -> dict[str, Any]:
    """Load the canary/denylist registry. Missing file → empty registry."""
    path = Path(registry_path) if registry_path is not None else _default_registry()
    if not path.is_file():
        return _empty_registry()
    data = json.loads(path.read_text(encoding="utf-8"))
    registry = _empty_registry()
    if isinstance(data, dict):
        canaries = data.get("canaries")
        if isinstance(canaries, dict):
            registry["canaries"] = canaries
        denylist = data.get("denylist")
        if isinstance(denylist, list):
            registry["denylist"] = denylist
    return registry


def _save_registry(registry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, indent=2, sort_keys=True), encoding="utf-8")


# --- canary seeding ---------------------------------------------------------


def seed_canary(
    vault_root: Path | str,
    matter_id: str,
    registry_path: Path | str | None = None,
) -> str:
    """Write ``<vault>/.canary`` and register token -> matter_id. Returns the token."""
    token = f"{CANARY_PREFIX}{matter_id}-{secrets.token_hex(16)}"
    canary_path = safe_vault_path(vault_root, CANARY_FILE)
    canary_path.write_text(token + "\n", encoding="utf-8")

    reg_path = Path(registry_path) if registry_path is not None else _default_registry()
    registry = load_registry(reg_path)
    registry["canaries"][token] = matter_id
    _save_registry(registry, reg_path)
    return token


# --- fail-closed grep -------------------------------------------------------


def _git_paths(repo_root: Path, *args: str) -> list[str]:
    """Run a path-listing git command with NUL-delimited output.

    ``-z`` is not optional here. Without it git applies ``core.quotePath`` (on by
    default) and emits a C-quoted, backslash-escaped literal — ``"na\\303\\257ve.txt"``,
    quotes included — for any path with a non-ASCII, quote, backslash, or control
    character in it. That string names no real file, so the scanner used to skip it as
    a staged deletion. A legal corpus is full of such names (``Müller``, ``Peña``, a
    smart quote), and each one was a hole straight through the only leak blocker.
    """
    out = subprocess.run(
        ["git", "-C", str(repo_root), *args, "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def _tracked_files(repo_root: Path) -> list[str]:
    tracked = _git_paths(repo_root, "ls-files")
    staged = _git_paths(repo_root, "diff", "--cached", "--name-only")
    return sorted({*tracked, *staged})


def privacy_grep(
    repo_root: Path | str,
    registry_path: Path | str | None = None,
) -> list[Finding]:
    """Scan git-tracked + staged files for registered canaries and denylist strings.

    Fails closed: an unreadable file, a binary that cannot be decoded, or a symlink
    that escapes the repo is reported as an ``unscannable`` finding (a failure). An
    internal symlink is skipped ONLY when its target is itself tracked — that is the
    entire justification for skipping it, and it has to be checked, not assumed.
    """
    root = Path(repo_root)
    root_real = Path(os.path.realpath(root))
    registry = load_registry(registry_path)
    tokens = list(registry["canaries"].keys())
    denylist = [s for s in registry["denylist"] if s]

    findings: list[Finding] = []
    entries = _tracked_files(root)
    tracked = set(entries)
    for rel in entries:
        full = root / rel
        # lstat first, and distinguish its failures. `Path.exists()`/`is_symlink()`
        # swallow every OSError, so an entry the process cannot stat (an unsearchable
        # parent directory) used to read as "staged deletion" and be skipped silently.
        try:
            st = full.lstat()
        except FileNotFoundError:
            continue  # staged deletion — nothing to leak
        except OSError as exc:
            findings.append(Finding(rel, "unscannable", f"unstattable: {exc}"))
            continue
        if stat.S_ISLNK(st.st_mode):
            target = Path(os.path.realpath(full))
            inside = target == root_real or root_real in target.parents
            if not (inside and target.is_file()):
                findings.append(Finding(rel, "unscannable", "symlink escapes repo (fail closed)"))
                continue
            # "The target is scanned on its own entry" is only true if the target IS an
            # entry. A link to an UNTRACKED path inside the repo — a gitignored scratch
            # file, `matters/`, a build dir — was skipped on that reasoning while nothing
            # else ever looked at it: a hole straight through the leak scanner, sitting
            # under a committed name. Scan it here, through the link, like any file.
            if target.relative_to(root_real).as_posix() in tracked:
                continue  # covered by the target's own tracked entry
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            findings.append(Finding(rel, "unscannable", "binary/undecodable content"))
            continue
        except OSError as exc:
            findings.append(Finding(rel, "unscannable", f"unreadable: {exc}"))
            continue
        for token in tokens:
            if token in text:
                matter_id = registry["canaries"][token]
                findings.append(Finding(rel, "canary", f"canary token for {matter_id}"))
        for needle in denylist:
            if needle in text:
                findings.append(Finding(rel, "denylist", f"denylist string {needle!r}"))
    return findings
