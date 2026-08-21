"""Immutable launch inputs for one run."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mootloop.models.common import MatterId, RunId, StrictModel, VersionedModel
from mootloop.models.corpus import Manifest
from mootloop.models.facts import Fact
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestSet
from mootloop.models.rubric import Rubric
from mootloop.models.run import PersonaName
from mootloop.models.task import TaskAdapterConfig
from mootloop.models.taskspec import TaskSpec

SCHEMA_VERSION = "1.0"
ContextSourceKind = Literal[
    "matter_config",
    "task_adapter",
    "rubric",
    "rubric_lock",
    "fact_repository",
    "request_set",
    "task_spec",
    "corpus_manifest",
    "corpus_content",
    "persona_body",
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

    doc_id: str
    locator: str
    sha256: str
    text: str


class CorpusSnapshot(VersionedModel):
    """Normalized corpus content stored beside, and hashed by, the manifest."""

    schema_version: str = SCHEMA_VERSION
    documents: list[CorpusTextSnapshot] = Field(default_factory=list)


class RunContextManifest(VersionedModel):
    """The complete set of currently-supported inputs approved at run launch."""

    schema_version: str = SCHEMA_VERSION
    run_id: RunId
    matter_id: MatterId
    task: str
    task_spec: TaskSpec | None = None
    adapter_config: TaskAdapterConfig
    adapter_behavior: AdapterBehavior
    persona_bodies: dict[PersonaName, str]
    rubric: Rubric
    request_sets: list[RequestSet] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    corpus_manifest: Manifest = Field(default_factory=Manifest)
    corpus_snapshot_sha256: str
    matter_config: MatterConfig
    effective_mode: Literal["autonomous", "gated", "observed"]
    max_attempts: int = Field(ge=1)
    tier_models: dict[str, str]
    sources: list[ContextSource]
