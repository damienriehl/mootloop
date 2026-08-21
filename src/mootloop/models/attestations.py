"""Append-only attorney review commitments and deterministic export seals.

A v2 attestation commits to the reviewed master plus the citation ledger, journal,
decisions, launch-snapshotted facts, and access-audit prefix. A linked export seal
then records the exact delivered artifact set. Later evidence or artifact mutation
re-imposes DRAFT without rewriting history; legacy records remain parseable but are
not accepted as current commitments.
"""

from __future__ import annotations

from typing import Literal

from mootloop.models.common import RunId, StrictModel, VersionedModel, canonical_json_sha256

SCHEMA_VERSION = "2.0"
EXPORT_SEAL_SCHEMA_VERSION = "1.0"
INTEGRITY_STATUS_SCHEMA_VERSION = "1.0"

AttestationCheckStatus = Literal["valid", "invalidated", "missing"]


class ArtifactDigest(StrictModel):
    """Exact bytes and vault-relative identity of one sealed export artifact."""

    path: str
    sha256: str
    size_bytes: int


class Attestation(VersionedModel):
    """One append-only attestation-manifest record (an attest or an invalidation)."""

    schema_version: str = SCHEMA_VERSION
    # Absent on schema 1.0 records, whose master hash covered only md-master.
    # Keep parsing those append-only records, but never treat their hash as current.
    hash_scope: str | None = None
    attestation_id: str
    run_id: RunId
    master_sha256: str
    ledger_head_sha256: str
    journal_sha256: str | None = None
    decisions_sha256: str | None = None
    fact_state_sha256: str | None = None
    access_audit_head_sha256: str | None = None
    commitment_sha256: str | None = None
    reviewer: str
    attested_at: str
    valid: bool = True
    reason: str | None = None

    def expected_commitment_sha256(self) -> str:
        """Digest every persisted field except the digest itself."""
        return canonical_json_sha256(
            self.model_dump(mode="json", exclude={"commitment_sha256"})
        )


class ExportSeal(VersionedModel):
    """A deterministic export manifest linked to one attorney attestation."""

    schema_version: str = EXPORT_SEAL_SCHEMA_VERSION
    seal_id: str
    run_id: RunId
    attestation_id: str
    attestation_commitment_sha256: str
    sealed_at: str
    artifacts: list[ArtifactDigest]
    export_set_sha256: str

    def expected_export_set_sha256(self) -> str:
        return canonical_json_sha256(
            [artifact.model_dump(mode="json") for artifact in self.artifacts]
        )


class ReviewIntegrityStatus(VersionedModel):
    """Read-only current attorney-commitment and clean-export integrity state."""

    schema_version: str = INTEGRITY_STATUS_SCHEMA_VERSION
    run_id: RunId
    attestation_status: AttestationCheckStatus
    attestation_reason: str | None = None
    export_seal_status: AttestationCheckStatus
    export_seal_reason: str | None = None
    latest_attestation: Attestation | None = None
    latest_export_seal: ExportSeal | None = None
