"""Attestation manifest vocabulary (plan D9/H8): the append-only record that binds a
reviewer to the exact bytes they attested.

An attestation captures the canonicalized md-master hash + the citation-ledger head
hash at attest time. A later mismatch (a post-attestation edit) re-imposes DRAFT and
logs an invalidation record — the ledger is append-only, so nothing is rewritten.
"""

from __future__ import annotations

from typing import Literal

from mootloop.models.common import VersionedModel

SCHEMA_VERSION = "1.0"

AttestationCheckStatus = Literal["valid", "invalidated", "missing"]


class Attestation(VersionedModel):
    """One append-only attestation-manifest record (an attest or an invalidation)."""

    schema_version: str = SCHEMA_VERSION
    attestation_id: str
    run_id: str
    master_sha256: str
    ledger_head_sha256: str
    reviewer: str
    attested_at: str
    valid: bool = True
    reason: str | None = None
