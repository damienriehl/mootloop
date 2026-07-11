"""Secrets loader + redaction (plan vault-boundary rules / D3).

Secrets live ONLY in ``~/.mootloop/secrets.env`` (``KEY=VALUE`` lines) or the process
environment — never in ``matter.yaml``, config, or the vault. This module reads them
and NEVER logs a value. `redact` scrubs secret-shaped strings from anything bound for
the journal or an artifact, so a token can never leak into an auditable trace.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SECRETS_FILE = Path.home() / ".mootloop" / "secrets.env"

# Secret-shaped patterns scrubbed from any text written to journal/artifacts. The
# CourtListener token is 40 lowercase hex chars; ``Token``/``Bearer`` headers and
# ``sk-`` keys cover the common shapes.
_REDACT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(Token|Bearer)\s+\S+"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b[0-9a-f]{40}\b"),
)

_REDACTED = "***REDACTED***"


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


def redact(text: str, *, extra: tuple[str, ...] = ()) -> str:
    """Scrub secret-shaped substrings (and any explicit ``extra`` values) from ``text``.

    Use before writing anything derived from an HTTP request/response to the journal
    or an artifact. Idempotent and safe on text with no secrets.
    """
    scrubbed = text
    for value in extra:
        if value:
            scrubbed = scrubbed.replace(value, _REDACTED)
    for pattern in _REDACT_PATTERNS:
        scrubbed = pattern.sub(_REDACTED, scrubbed)
    return scrubbed
