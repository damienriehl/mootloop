"""Fact-repository vocabulary: a `Provenance` link into the corpus and a versioned
`Fact`. Facts live in an append-only JSONL log (see `mootloop.facts`); these are the
record shapes written one-per-line.

A `Fact` with empty ``provenance`` is unsupported and should be flagged downstream.
``version`` starts at 1 and increments on revision; ``superseded_by`` names the
fact that replaced this one (set on the predecessor's *re-emitted* line, never by
mutating the original — the fold resolves it). ``supersedes`` is the same edge
written the other way, on the successor, so ONE durable line carries the whole
transition and a crash between the two appends cannot leave it ambiguous.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mootloop.models.common import DocId, FactId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.1"

FactReviewStatus = Literal["pending", "accepted", "rejected"]
FactQuestionKind = Literal[
    "review_fact",
    "missing_provenance",
    "repair_provenance",
    "uncovered_document",
]


class Provenance(StrictModel):
    """A supporting citation: a verbatim ``quote`` from corpus document ``doc_id``."""

    doc_id: DocId
    quote: str
    location_hint: str | None = None


class Fact(VersionedModel):
    """One version of one logical fact.

    ``provenance`` may be empty (unsupported → flagged). ``confidence`` is in
    ``[0, 1]``. Each `RESPONSE_ITEM` pins the fact ``version`` it grounded on.

    ``supersedes`` names the predecessor this version replaces. It is optional so
    every record written before it existed still validates; ``None`` on a v1 fact
    is the normal case, not a missing value.
    """

    schema_version: str = SCHEMA_VERSION
    fact_id: FactId
    statement: str
    provenance: list[Provenance] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    version: int = Field(ge=1)
    superseded_by: str | None = None
    supersedes: str | None = None
    review_status: FactReviewStatus = "accepted"
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    review_note: str | None = None


class FactQuestion(StrictModel):
    """One deterministic fact-preparation question for human review."""

    question_id: str
    kind: FactQuestionKind
    prompt: str
    fact_id: FactId | None = None
    doc_id: DocId | None = None


class FactInterview(StrictModel):
    """Current fact-preparation readiness and its unresolved questions."""

    run_visible_fact_count: int
    questions: list[FactQuestion] = Field(default_factory=list)
