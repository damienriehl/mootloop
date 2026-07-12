"""Secrets loader + redaction (plan vault-boundary rules / D3).

Secrets live ONLY in ``~/.mootloop/secrets.env`` (``KEY=VALUE`` lines) or the process
environment — never in ``matter.yaml``, config, or the vault. This module reads them
and NEVER logs a value. `redact` scrubs secret-shaped strings from anything bound for
the journal or an artifact, so a token can never leak into an auditable trace.
"""

from __future__ import annotations

import os
import re
import secrets as _secrets
from pathlib import Path

SECRETS_FILE = Path.home() / ".mootloop" / "secrets.env"

# The HMAC signing key for short-expiry deliverable download links (plan FD-7 / P-37).
# Loaded from the service-user secrets (never hard-coded); derived + persisted on first
# use if absent so the hosted tier can mint links without manual provisioning.
DOWNLOAD_SIGNING_KEY = "MOOTLOOP_DOWNLOAD_SIGNING_KEY"

# Secret-shaped patterns scrubbed from any text written to journal/artifacts. The
# CourtListener token is 40 lowercase hex chars; ``Token``/``Bearer`` headers and
# ``sk-`` keys cover the common shapes. FD-3 adds the hosted-tier sinks' new shapes:
# Google OAuth refresh tokens (``1//…``) and Claude Code OAuth/API tokens
# (``sk-ant-oat…``/``sk-ant-ort…``/``sk-ant-api…``) — the crown-jewel subscription
# token among them.
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(Token|Bearer)\s+\S+"),
    re.compile(r"sk-ant-[A-Za-z0-9]{2,}-[A-Za-z0-9_\-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"1//[A-Za-z0-9_\-]+"),
    re.compile(r"\b[0-9a-f]{40}\b"),
)

_REDACTED = "***REDACTED***"

# Exact live secret values registered for verbatim redaction. FD-3 requires scrubbing
# the actual token strings (not just their shapes) at every outbound sink, so a value
# that slips a pattern (e.g. an ntfy topic or a rotated key) still never leaks.
_REGISTERED_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    """Register an exact secret value to be redacted verbatim wherever it appears.

    Idempotent. Empty/blank values are ignored (they would otherwise match the whole
    string). The value itself is never logged; only its literal is scrubbed later.
    """
    if value and value.strip():
        _REGISTERED_SECRETS.add(value)


def _read_secrets_file(path: Path = SECRETS_FILE) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip("'\"")
    return values


def load_secret(key: str, *, secrets_file: Path = SECRETS_FILE) -> str | None:
    """Resolve ``key`` from ``~/.mootloop/secrets.env`` first, then the environment.

    Returns ``None`` if unset (callers fail closed — a missing token means the check
    stays ``pending``, never a false ``verified``). Never logs the value.
    """
    from_file = _read_secrets_file(secrets_file).get(key)
    if from_file:
        return from_file
    return os.environ.get(key)


def load_or_create_signing_key(
    key: str = DOWNLOAD_SIGNING_KEY, *, secrets_file: Path = SECRETS_FILE
) -> str:
    """Return the download-link HMAC key, deriving + persisting it on first use.

    Resolves ``key`` via `load_secret` (secrets file, then env). If unset, mints a fresh
    urlsafe token and appends it to ``secrets_file`` under the service-user convention
    (dir ``0700``, file ``0600``) so subsequent processes reuse the same key — links
    minted before a restart stay verifiable. The value is registered for redaction and
    never logged.
    """
    existing = load_secret(key, secrets_file=secrets_file)
    if existing:
        register_secret(existing)
        return existing
    value = _secrets.token_urlsafe(32)
    secrets_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(secrets_file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(f"{key}={value}\n")
    os.chmod(secrets_file, 0o600)
    register_secret(value)
    return value


def redact(text: str, *, extra: tuple[str, ...] = ()) -> str:
    """Scrub secret-shaped substrings (and any explicit ``extra`` values) from ``text``.

    Use before writing anything derived from an HTTP request/response to the journal
    or an artifact. Idempotent and safe on text with no secrets.
    """
    scrubbed = text
    # Longest first so a longer secret is replaced before any shorter substring of it.
    literals = sorted({*extra, *_REGISTERED_SECRETS}, key=len, reverse=True)
    for value in literals:
        if value:
            scrubbed = scrubbed.replace(value, _REDACTED)
    for pattern in _REDACT_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed
