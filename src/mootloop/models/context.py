"""Immutable launch inputs for one run."""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import ConfigDict, Field, model_validator

from mootloop.models.common import (
    MATTER_ID_PATTERN,
    DocId,
    MatterId,
    RunId,
    StrictModel,
    VersionedModel,
)
from mootloop.models.config import ResolvedRunConfig
from mootloop.models.corpus import Manifest
from mootloop.models.facts import Fact
from mootloop.models.matter import MatterConfig
from mootloop.models.pipeline import ResolvedPipeline
from mootloop.models.requests import RequestSet
from mootloop.models.rubric import Rubric
from mootloop.models.run import PersonaName
from mootloop.models.task import TaskAdapterConfig
from mootloop.models.taskspec import TaskSpec, TaskSpecLock

SCHEMA_VERSION = "1.5"
CORPUS_SNAPSHOT_SCHEMA_VERSION = "1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTRIBUTION_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,127}$")
ContextSourceKind = Literal[
    "matter_config",
    "task_adapter",
    "rubric",
    "rubric_lock",
    "fact_repository",
    "request_set",
    "task_spec",
    "task_spec_lock",
    "corpus_manifest",
    "corpus_content",
    "persona_body",
    "context_contribution",
]
ContextContributionKind = Literal["board", "learning", "context_note", "firm_playbook"]
ContextApprovalState = Literal["approved", "accepted", "pending", "rejected"]
ContextPermission = Literal["matter_confidential", "privileged"]
ContextTrust = Literal["untrusted_data"]
ContextSharingScope = Literal["matter", "firm", "public_area"]
ContextExclusionReason = Literal[
    "not_approved",
    "wrong_matter",
    "wrong_task",
    "ethical_wall",
]
AssembledContextKind = Literal[
    "fact",
    "corpus_passage",
    "board",
    "learning",
    "context_note",
    "firm_playbook",
]


class ContextSource(StrictModel):
    """One launch source and its exact content digest."""

    kind: ContextSourceKind
    locator: str
    sha256: str


class AdapterBehavior(StrictModel):
    """Resolved Python adapter behavior, frozen as prompt-bearing strings."""

    task: str
    draft_directive: str
    judge_question: str


class CorpusTextSnapshot(StrictModel):
    """One normalized corpus document captured exactly at launch."""

    doc_id: DocId
    locator: str
    sha256: str
    text: str


class CorpusSnapshot(VersionedModel):
    """Normalized corpus content stored beside, and hashed by, the manifest."""

    schema_version: str = CORPUS_SNAPSHOT_SCHEMA_VERSION
    documents: list[CorpusTextSnapshot] = Field(default_factory=list)


class MatterContextMemory(VersionedModel):
    """Trusted human provenance sidecar for the matter's bounded context.md memory."""

    schema_version: Literal["1.0"] = "1.0"
    source_matter_id: MatterId = Field(pattern=MATTER_ID_PATTERN)
    content_sha256: str
    approved_by: str = Field(min_length=1, max_length=320)
    approved_at: str

    @model_validator(mode="after")
    def validate_digest_and_actor(self) -> MatterContextMemory:
        if not _SHA256_RE.fullmatch(self.content_sha256):
            raise ValueError("content_sha256 must be lowercase SHA-256")
        if not self.approved_by.strip():
            raise ValueError("approved_by must identify the human actor")
        return self


class ContextContribution(StrictModel):
    """One candidate launch contribution; never trusted as prompt instructions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contribution_id: str
    kind: ContextContributionKind
    text: str = Field(min_length=1)
    sha256: str
    provenance_locator: str = Field(min_length=1, max_length=1024)
    source_matter_id: MatterId
    task_scope: tuple[str, ...] = ()
    persona_scope: tuple[PersonaName, ...] = ()
    trust: ContextTrust = "untrusted_data"
    permission: ContextPermission
    approval_state: ContextApprovalState
    sharing_scope: ContextSharingScope = "matter"
    excluded_matter_ids: tuple[MatterId, ...] = ()

    @model_validator(mode="after")
    def validate_identity_and_digest(self) -> ContextContribution:
        if not _CONTRIBUTION_ID_RE.fullmatch(self.contribution_id):
            raise ValueError("contribution_id must be a stable lowercase identifier")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 must be exactly 64 lowercase hexadecimal characters")
        expected = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.sha256 != expected:
            raise ValueError("sha256 does not match the exact UTF-8 contribution text")
        if any(char in self.provenance_locator for char in "\r\n\x00"):
            raise ValueError("provenance_locator may not contain control-line characters")
        if len(set(self.task_scope)) != len(self.task_scope):
            raise ValueError("task_scope may not contain duplicates")
        if len(set(self.persona_scope)) != len(self.persona_scope):
            raise ValueError("persona_scope may not contain duplicates")
        if len(set(self.excluded_matter_ids)) != len(self.excluded_matter_ids):
            raise ValueError("excluded_matter_ids may not contain duplicates")
        if self.sharing_scope == "matter" and self.excluded_matter_ids:
            raise ValueError("matter-scoped contributions cannot declare ethical-wall exclusions")
        return self


class StoredContextContribution(VersionedModel):
    """Versioned top-level envelope for one write-once launch candidate source."""

    schema_version: str = "1.0"
    contribution: ContextContribution


class ContextExclusion(StrictModel):
    """Text-free audit record proving why a candidate did not enter a run snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contribution_id: str
    kind: ContextContributionKind
    reason: ContextExclusionReason


class AssembledContextItem(StrictModel):
    """One bounded prompt-data item with enough metadata to audit its authority."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context_id: str
    kind: AssembledContextKind
    text: str
    sha256: str
    provenance_locator: str
    source_matter_id: MatterId
    task_scope: tuple[str, ...] = ()
    persona_scope: tuple[PersonaName, ...] = ()
    trust: ContextTrust = "untrusted_data"
    permission: ContextPermission

    @model_validator(mode="after")
    def validate_content(self) -> AssembledContextItem:
        if not self.context_id or any(char in self.context_id for char in "\r\n\x00"):
            raise ValueError("context_id must be a non-empty single-line identifier")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("sha256 must be exactly 64 lowercase hexadecimal characters")
        if self.sha256 != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("sha256 does not match the exact UTF-8 context text")
        if not self.provenance_locator or any(
            char in self.provenance_locator for char in "\r\n\x00"
        ):
            raise ValueError("provenance_locator must be a non-empty single-line locator")
        return self


class RunContextManifest(VersionedModel):
    """The complete set of currently-supported inputs approved at run launch."""

    schema_version: str = SCHEMA_VERSION
    run_id: RunId
    matter_id: MatterId
    task: str
    task_spec: TaskSpec | None = None
    task_spec_lock: TaskSpecLock | None = None
    adapter_config: TaskAdapterConfig
    resolved_config: ResolvedRunConfig
    pipeline: ResolvedPipeline
    adapter_behavior: AdapterBehavior
    persona_bodies: dict[PersonaName, str]
    rubric: Rubric
    request_sets: list[RequestSet] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    corpus_manifest: Manifest = Field(default_factory=Manifest)
    corpus_snapshot_sha256: str
    context_contributions: list[ContextContribution] = Field(default_factory=list)
    context_exclusions: list[ContextExclusion] = Field(default_factory=list)
    matter_config: MatterConfig
    effective_mode: Literal["autonomous", "gated", "observed"]
    max_attempts: int = Field(ge=1)
    tier_models: dict[str, str]
    sources: list[ContextSource]

    @model_validator(mode="after")
    def validate_task_spec_lock_identity(self) -> RunContextManifest:
        if self.task_spec_lock is None:
            return self
        if self.task_spec is None:
            raise ValueError("task_spec_lock requires a captured task_spec")
        lock = self.task_spec_lock
        if (
            lock.task_spec_id != self.task_spec.task_spec_id
            or lock.matter_id != self.matter_id
            or lock.task != self.task
        ):
            raise ValueError("task_spec_lock identity does not match the run manifest")
        return self
