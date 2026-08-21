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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mootloop import secrets as secret_store
from mootloop.errors import OutboundPrivacyError
from mootloop.models.common import PublicText
from mootloop.runtime import RUNTIME_MODE_ENV, RuntimeMode
from mootloop.secrets import SECRETS_FILE
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


def _load_hosted_outbound_registry(
    registry_path: Path | str | None,
) -> dict[str, Any]:
    path = Path(registry_path) if registry_path is not None else _default_registry()
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise OutboundPrivacyError(
                "hosted outbound policy requires a regular canary registry file"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
    except OutboundPrivacyError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise OutboundPrivacyError(
            "hosted outbound policy requires a readable valid canary registry"
        ) from exc
    if not isinstance(data, dict):
        raise OutboundPrivacyError("hosted canary registry must be a JSON object")
    canaries = data.get("canaries")
    denylist = data.get("denylist")
    if not isinstance(canaries, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in canaries.items()
    ):
        raise OutboundPrivacyError("hosted canary registry has invalid canaries")
    if not isinstance(denylist, list) or not all(isinstance(value, str) for value in denylist):
        raise OutboundPrivacyError("hosted canary registry has invalid denylist")
    return {"canaries": canaries, "denylist": denylist}


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


# --- outbound confidentiality gate -----------------------------------------


@dataclass(frozen=True)
class _OutboundPolicy:
    canaries: tuple[str, ...]
    denylist: tuple[str, ...]
    contains_secret: Callable[[str], bool]

    @classmethod
    def load(
        cls,
        *,
        registry_path: Path | str | None,
        secrets_file: Path,
    ) -> _OutboundPolicy:
        registry = (
            _load_hosted_outbound_registry(registry_path)
            if os.environ.get(RUNTIME_MODE_ENV) == RuntimeMode.HOSTED
            else load_registry(registry_path)
        )
        return cls(
            canaries=tuple(token for token in registry["canaries"] if token),
            denylist=tuple(value for value in registry["denylist"] if value),
            contains_secret=secret_store.exact_secret_matcher(secrets_file=secrets_file),
        )

    def scrub(self, text: str) -> PublicText:
        if any(token in text for token in self.canaries):
            raise OutboundPrivacyError("outbound payload contains a registered matter canary")
        if any(value in text for value in self.denylist):
            raise OutboundPrivacyError("outbound payload contains a denylisted value")
        if self.contains_secret(text):
            raise OutboundPrivacyError("outbound payload contains an exact secret value")
        return PublicText(secret_store.redact(text))


def _scrub_outbound_value(
    value: Any,
    *,
    policy: _OutboundPolicy,
) -> Any:
    if isinstance(value, str):
        return policy.scrub(value)
    if isinstance(value, dict):
        scrubbed: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("outbound payload mapping keys must be strings")
            scrubbed[policy.scrub(key)] = _scrub_outbound_value(item, policy=policy)
        return scrubbed
    if isinstance(value, (list, tuple)):
        return [_scrub_outbound_value(item, policy=policy) for item in value]
    return value


def scrub_outbound(
    text: str,
    *,
    registry_path: Path | str | None = None,
    secrets_file: Path = SECRETS_FILE,
) -> PublicText:
    """Block tripwires/exact secrets, redact secret shapes, then trust-convert text."""
    policy = _OutboundPolicy.load(
        registry_path=registry_path,
        secrets_file=secrets_file,
    )
    return policy.scrub(text)


def serialize_outbound(
    payload: Any,
    *,
    registry_path: Path | str | None = None,
    secrets_file: Path = SECRETS_FILE,
) -> PublicText:
    """Return compact JSON only after recursively checking every string value.

    Canary and exact-secret checks happen before ``json.dumps``. The only successful
    return type is ``PublicText``, making this the shared trust-conversion point for
    SSE, notifications, and future connector payloads.
    """
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    policy = _OutboundPolicy.load(
        registry_path=registry_path,
        secrets_file=secrets_file,
    )
    scrubbed = _scrub_outbound_value(
        payload,
        policy=policy,
    )
    return PublicText(
        json.dumps(scrubbed, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    )


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
