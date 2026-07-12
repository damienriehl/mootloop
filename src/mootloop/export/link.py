"""Signed, short-expiry deliverable download links (plan FD-7 / P-37).

Deliverable downloads are permitted (admin=client) but every one is logged in the
access audit and gated on the run's export-readiness for clean (non-DRAFT) work
product. This module mints a tamper-evident token bound to a single
(matter, run, deliverable) audience with a bounded expiry, validates it on the way back
in, and resolves the deliverable's on-disk path — all through `safe_vault_path`.

Gating rule (the DRAFT-vs-clean decision, single source of truth here):
  * a **clean** deliverable — a ``.docx`` with no ``.DRAFT.`` infix — requires
    ``gate_ledger.export_ready`` at BOTH mint and download time (fail closed);
  * a **DRAFT** deliverable (``.DRAFT.docx``) and informational artifacts
    (markdown masters, ``audit-log.json``) are always downloadable.

The download handler MUST record the access audit FIRST and fail closed (a download that
cannot be recorded is never served) — this module gives it `_resolve_deliverable`; the
route calls `mootloop.web.audit.record_download_audit` before streaming.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from mootloop import gate_ledger
from mootloop.errors import ExportLinkError, ExportNotReadyError
from mootloop.export import deliverables_dir
from mootloop.vault import safe_vault_path

# Links are deliberately short-lived (plan FD-7): at most ten minutes, single audience.
MAX_TTL_SECONDS = 600.0


# --- deliverable listing + gating -------------------------------------------


@dataclass(frozen=True)
class DeliverableEntry:
    """One downloadable deliverable file under ``deliverables/<run-id>/``."""

    name: str  # path relative to the run's deliverable dir (POSIX, may contain ``/``)
    size_bytes: int
    is_draft: bool
    requires_export_ready: bool


def _gating(name: str) -> tuple[bool, bool]:
    """(is_draft, requires_export_ready) for a deliverable file name."""
    lower = name.lower()
    if ".draft." in lower:
        return True, False
    if lower.endswith(".docx"):
        # A clean court-formatted DOCX is real filed work product — gate it.
        return False, True
    # Markdown masters, verification page, audit-log.json: informational, never gated.
    return False, False


def list_deliverables(vault_root: Path | str, run_id: str) -> list[DeliverableEntry]:
    """Every deliverable file for a run, sorted by name (empty if none built yet)."""
    base = deliverables_dir(vault_root, run_id)
    if not base.is_dir():
        return []
    out: list[DeliverableEntry] = []
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        name = path.relative_to(base).as_posix()
        is_draft, requires = _gating(name)
        out.append(DeliverableEntry(name, path.stat().st_size, is_draft, requires))
    return out


def _resolve_deliverable(vault_root: Path | str, run_id: str, name: str) -> Path:
    """Resolve a deliverable name to its on-disk path (containment-checked).

    ``name`` is a run-relative POSIX path (it may name a ``docx/…`` subfile);
    `safe_vault_path` rejects any part that escapes the run's deliverable dir.
    """
    parts = [p for p in name.split("/") if p]
    if not parts:
        raise ExportLinkError(f"empty deliverable name for run {run_id!r}")
    path = safe_vault_path(vault_root, "deliverables", run_id, *parts)
    if not path.is_file():
        raise ExportLinkError(f"unknown deliverable {name!r} for run {run_id!r}")
    return path


def _require_ready_if_clean(vault_root: Path | str, run_id: str, name: str) -> None:
    """Enforce the clean-file export gate; DRAFT/informational files pass through."""
    _is_draft, requires = _gating(name)
    if not requires:
        return
    ready, blockers = gate_ledger.export_ready(vault_root, run_id)
    if not ready:
        raise ExportNotReadyError(name, blockers)


# --- token mint / validate --------------------------------------------------


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64u_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


class LinkSigner:
    """HMAC-SHA256 signer/verifier for download tokens (key from secrets, never hard-coded)."""

    def __init__(self, key: str) -> None:
        self._key = key.encode("utf-8")

    def _mac(self, body: str) -> str:
        digest = hmac.new(self._key, body.encode("ascii"), hashlib.sha256).digest()
        return _b64u(digest)

    def sign(self, payload: dict[str, Any]) -> str:
        """Encode + HMAC a claims payload as ``<body>.<mac>``."""
        body = _b64u(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        return f"{body}.{self._mac(body)}"

    def verify(self, token: str) -> dict[str, Any]:
        """Recompute + constant-time-compare the MAC; return the decoded claims.

        Raises `ExportLinkError` on any malformed/tampered token (fail closed).
        """
        try:
            body, mac = token.split(".", 1)
        except ValueError as exc:
            raise ExportLinkError("malformed download token") from exc
        if not hmac.compare_digest(mac, self._mac(body)):
            raise ExportLinkError("download token signature mismatch")
        try:
            claims = json.loads(_b64u_decode(body))
        except (ValueError, json.JSONDecodeError) as exc:
            raise ExportLinkError("undecodable download token") from exc
        if not isinstance(claims, dict):
            raise ExportLinkError("download token payload is not an object")
        return claims


@dataclass(frozen=True)
class SignedLink:
    """A minted download link: the opaque token, its URL, audience, and expiry."""

    token: str
    url: str
    matter_id: str
    run_id: str
    doc: str
    is_draft: bool
    expires_at: str


@dataclass(frozen=True)
class TokenClaims:
    """The validated audience of a download token."""

    matter_id: str
    run_id: str
    doc: str
    exp: float


def mint_link(
    vault_root: Path | str,
    matter_id: str,
    run_id: str,
    doc: str,
    now: str,
    signer: LinkSigner,
    *,
    ttl_seconds: float = MAX_TTL_SECONDS,
) -> SignedLink:
    """Mint a signed link bound to a single (matter, run, deliverable) with a bounded
    expiry. Raises `ExportNotReadyError` (clean file, run not export-ready) or
    `ExportLinkError` (unknown deliverable) — fail closed before any token is issued."""
    _resolve_deliverable(vault_root, run_id, doc)
    _require_ready_if_clean(vault_root, run_id, doc)
    ttl = min(max(ttl_seconds, 0.0), MAX_TTL_SECONDS)
    issued = datetime.fromisoformat(now)
    expires = issued + timedelta(seconds=ttl)
    payload = {"m": matter_id, "r": run_id, "d": doc, "exp": expires.timestamp()}
    token = signer.sign(payload)
    is_draft, _requires = _gating(doc)
    return SignedLink(
        token=token,
        url=f"/api/download?token={token}",
        matter_id=matter_id,
        run_id=run_id,
        doc=doc,
        is_draft=is_draft,
        expires_at=expires.isoformat(),
    )


def validate_token(token: str, now: str, signer: LinkSigner) -> TokenClaims:
    """Validate a download token's signature and expiry; return its audience.

    Raises `ExportLinkError` on a tampered token, a missing/typed-wrong claim, or an
    expired link (fail closed — the caller never streams on a raise)."""
    claims = signer.verify(token)
    try:
        matter_id = str(claims["m"])
        run_id = str(claims["r"])
        doc = str(claims["d"])
        exp = float(claims["exp"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ExportLinkError("download token is missing a required claim") from exc
    if datetime.fromisoformat(now).timestamp() > exp:
        raise ExportLinkError("download link has expired")
    return TokenClaims(matter_id=matter_id, run_id=run_id, doc=doc, exp=exp)


def resolve_for_download(vault_root: Path | str, claims: TokenClaims) -> Path:
    """Re-check the clean-file gate and resolve the deliverable path for streaming.

    Defense in depth: the export-ready gate is re-evaluated at download time, not only
    at mint, so a link minted while clean cannot serve a file the gate would now block.
    """
    _require_ready_if_clean(vault_root, claims.run_id, claims.doc)
    return _resolve_deliverable(vault_root, claims.run_id, claims.doc)
