"""Attestation manifest vocabulary (plan D9/H8): the append-only record that binds a
reviewer to the exact bytes they attested.

An attestation captures the citation-ledger head hash plus ``master_sha256``: the
canonicalized md-master bound to a digest of ``matter.yaml`` (see `mootloop.attest` —
the served document is rendered from both, so both must be attested). A later mismatch
(a post-attestation edit to either) re-imposes DRAFT and logs an invalidation record —
the ledger is append-only, so nothing is rewritten.
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
