"""Content-free commitments for protected attorney benchmark comparisons."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import (
    MATTER_ID_PATTERN,
    EvidencePackId,
    MatterId,
    RunId,
    StrictModel,
    VersionedModel,
)

BenchmarkVerdictValue = Literal["better", "equal", "worse"]
BenchmarkDimension = Literal[
    "legal_correctness",
    "grounding",
    "usability",
    "confidentiality",
]
BenchmarkChannel = Literal["api", "cli"]


class BenchmarkArtifactCommitment(StrictModel):
    """Exact-byte identity without work-product text or a revealing filename."""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class BenchmarkDimensionVerdict(StrictModel):
    """One bounded attorney comparison; no free-text matter content is persisted."""

    dimension: BenchmarkDimension
    verdict: BenchmarkVerdictValue


class BenchmarkEvidencePack(VersionedModel):
    """Immutable commitments needed to reproduce one candidate/baseline comparison."""

    schema_version: str = "1.0"
    evidence_pack_id: EvidencePackId = Field(
        pattern=r"^EP-mootloop-[a-z0-9][a-z0-9._-]{0,63}-[0-9]{3}$"
    )
    source_matter_id: MatterId = Field(pattern=MATTER_ID_PATTERN)
    run_id: RunId = Field(pattern=MATTER_ID_PATTERN)
    task: str = Field(min_length=1, max_length=128)
    created_at: str
    context_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rubric_id: str = Field(min_length=1, max_length=128)
    rubric_version: str = Field(min_length=1, max_length=64)
    candidate: BenchmarkArtifactCommitment
    baseline: BenchmarkArtifactCommitment


class BenchmarkVerdict(VersionedModel):
    """Append-only hard-human verdict linked to an immutable evidence pack."""

    schema_version: str = "1.0"
    verdict_id: str = Field(pattern=r"^benchmark-verdict-[0-9a-f]{16}$")
    evidence_pack_id: EvidencePackId
    evidence_pack_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_matter_id: MatterId = Field(pattern=MATTER_ID_PATTERN)
    run_id: RunId = Field(pattern=MATTER_ID_PATTERN)
    reviewer: str = Field(min_length=1, max_length=320)
    source: Literal["human"] = "human"
    channel: BenchmarkChannel
    recorded_at: str
    overall: BenchmarkVerdictValue
    dimensions: tuple[BenchmarkDimensionVerdict, ...]

    @model_validator(mode="after")
    def validate_human_verdict(self) -> BenchmarkVerdict:
        if not self.reviewer.strip():
            raise ValueError("reviewer must identify the human attorney")
        if not self.dimensions:
            raise ValueError("benchmark verdict requires at least one dimension")
        names = [item.dimension for item in self.dimensions]
        if len(names) != len(set(names)):
            raise ValueError("duplicate benchmark dimensions are not allowed")
        return self
