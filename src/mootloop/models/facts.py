"""Fact-repository vocabulary: a `Provenance` link into the corpus and a versioned
`Fact`. Facts live in an append-only JSONL log (see `mootloop.facts`); these are the
record shapes written one-per-line.

A `Fact` with empty ``provenance`` is unsupported and should be flagged downstream.
``version`` starts at 1 and increments on revision; ``superseded_by`` names the
fact that replaced this one (set on the predecessor's *re-emitted* line, never by
mutating the original — the fold resolves it).
"""

from __future__ import annotations

from pydantic import Field

from mootloop.models.common import DocId, FactId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"


class Provenance(StrictModel):
    """A supporting citation: a verbatim ``quote`` from corpus document ``doc_id``."""

    doc_id: DocId
    quote: str
    location_hint: str | None = None


class Fact(VersionedModel):
    """One version of one logical fact.

    ``provenance`` may be empty (unsupported → flagged). ``confidence`` is in
    ``[0, 1]``. Each `RESPONSE_ITEM` pins the fact ``version`` it grounded on.
    """

    schema_version: str = SCHEMA_VERSION
    fact_id: FactId
    statement: str
    provenance: list[Provenance] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    version: int = Field(ge=1)
    superseded_by: str | None = None
