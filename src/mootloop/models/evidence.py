"""Content-free derived trace metadata and immutable run evidence commitments."""

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
    canonical_json_sha256,
)
from mootloop.models.events import RunMode, RunStatus

SHA256_PATTERN = r"^[0-9a-f]{64}$"


class TraceNode(StrictModel):
    """One content-free node whose parent relation forms the run trace tree."""

    node_id: str = Field(min_length=1, max_length=256)
    parent_id: str | None = Field(default=None, max_length=256)
    kind: str = Field(min_length=1, max_length=128)
    sequence: int = Field(ge=0)
    source_path: str = Field(min_length=1, max_length=512)
    source_sha256: str = Field(pattern=SHA256_PATTERN)
    label: None = None


class TraceTree(VersionedModel):
    """A derived, content-free parent tree over exact journal event bytes."""

    schema_version: Literal["1.0"] = "1.0"
    source_matter_id: MatterId = Field(pattern=MATTER_ID_PATTERN)
    run_id: RunId = Field(pattern=MATTER_ID_PATTERN)
    generated_at: str
    nodes: tuple[TraceNode, ...]
    tree_sha256: str = Field(pattern=SHA256_PATTERN)

    def expected_tree_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json", exclude={"tree_sha256"}))

    @model_validator(mode="after")
    def validate_tree_sha256(self) -> TraceTree:
        if self.tree_sha256 != self.expected_tree_sha256():
            raise ValueError("tree_sha256 does not match the trace tree")
        return self


class EvidenceCommitment(StrictModel):
    """Exact bytes of one fixed, non-user-authored vault source path."""

    kind: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=SHA256_PATTERN)
    size_bytes: int = Field(ge=0)


class RunEvidencePack(VersionedModel):
    """Immutable replay commitments for one numbered run evidence snapshot."""

    schema_version: Literal["1.0"] = "1.0"
    evidence_pack_id: EvidencePackId = Field(
        pattern=r"^EP-mootloop-[a-z0-9][a-z0-9._-]{0,63}-[0-9]{3}$"
    )
    source_matter_id: MatterId = Field(pattern=MATTER_ID_PATTERN)
    run_id: RunId = Field(pattern=MATTER_ID_PATTERN)
    sequence: int = Field(ge=1, le=999)
    created_at: str
    generated_by: str = Field(min_length=1, max_length=320)
    channel: Literal["api", "cli"]
    trace_tree_sha256: str = Field(pattern=SHA256_PATTERN)
    trace_tree: TraceTree
    commitments: tuple[EvidenceCommitment, ...]
    pack_sha256: str = Field(pattern=SHA256_PATTERN)

    def expected_pack_sha256(self) -> str:
        return canonical_json_sha256(self.model_dump(mode="json", exclude={"pack_sha256"}))

    @model_validator(mode="after")
    def validate_commitments(self) -> RunEvidencePack:
        if self.trace_tree_sha256 != self.trace_tree.tree_sha256:
            raise ValueError("trace_tree_sha256 does not match the embedded trace tree")
        if self.run_id != self.trace_tree.run_id:
            raise ValueError("evidence pack and trace tree run_id differ")
        if self.source_matter_id != self.trace_tree.source_matter_id:
            raise ValueError("evidence pack and trace tree matter differ")
        if self.pack_sha256 != self.expected_pack_sha256():
            raise ValueError("pack_sha256 does not match the evidence pack")
        return self


class RunStatusSidecar(VersionedModel):
    """Machine-readable sidecar bound to the exact observed STATUS.md bytes."""

    schema_version: Literal["1.0"] = "1.0"
    source_matter_id: MatterId = Field(pattern=MATTER_ID_PATTERN)
    run_id: RunId = Field(pattern=MATTER_ID_PATTERN)
    task: str
    mode: RunMode
    status: RunStatus
    current_stage: str | None = None
    total_spend_usd: float = Field(ge=0)
    completed_turns: int = Field(ge=0)
    discarded_turns: int = Field(ge=0)
    context_manifest_sha256: str = Field(pattern=SHA256_PATTERN)
    human_view_sha256: str = Field(pattern=SHA256_PATTERN)
