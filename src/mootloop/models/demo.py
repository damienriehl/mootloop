"""Public, reviewed projections. These records contain no operational vault state."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, StringConstraints, field_validator, model_validator

from mootloop.models.common import MATTER_ID_PATTERN, StrictModel, VersionedModel

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identity = Annotated[str, Field(pattern=MATTER_ID_PATTERN)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Collection = Literal["synthetic", "public-record", "business"]
DemoTask = Literal[
    "discovery-responses",
    "complaint",
    "motion",
    "appellate-brief",
    "oral-argument",
    "business-advice",
]


class SourceReference(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    source_id: Identity
    title: Text
    url: Text
    available_on: date | None
    filed_on: date | None = None
    effective_on: date | None = None
    retrieved_on: date
    sha256: Digest
    locator: Text
    classification: Literal["record", "allegation", "disputed", "law", "hypothetical", "outcome"]
    redistribution: Literal["link-only", "public-domain", "licensed-excerpt", "original-summary"]
    redistribution_basis: Text

    @field_validator("url")
    @classmethod
    def https_link(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or any(ord(c) < 33 for c in value)
            or "\\" in value
        ):
            raise ValueError("source URL must be an HTTPS link without credentials")
        return value

    def eligible_at(self, cutoff: date) -> bool:
        return (
            self.classification not in {"outcome", "hypothetical"}
            and self.available_on is not None
            and self.available_on <= cutoff
            and (self.effective_on is None or self.effective_on <= cutoff)
        )


class DemoDescriptor(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    demo_id: Identity
    title: Text
    introduction: Text
    collection: Collection
    practice_area: Text
    work_product: Text
    jurisdiction: Text
    court_level: Literal[
        "state-trial",
        "federal-trial",
        "state-appellate",
        "federal-appellate",
        "supreme",
        "advisory",
    ]
    task: DemoTask
    cutoff: date | None = None
    represented_side: Text | None = None

    @model_validator(mode="after")
    def check_kind(self) -> DemoDescriptor:
        if self.collection == "public-record" and (
            self.cutoff is None or not self.represented_side
        ):
            raise ValueError("public-record demo requires cutoff and represented_side")
        if (self.collection == "business") != (self.task == "business-advice"):
            raise ValueError("business collection requires business-advice task")
        if (self.collection == "business") != (self.court_level == "advisory"):
            raise ValueError("business collection requires advisory court level")
        return self


class DemoProvenance(StrictModel):
    preparation: Literal["scripted-replay", "model-generated"]
    authorship: Text
    editorial_changes: Text
    provider_calls: int = Field(ge=0)
    attorney_approval: Literal[False] = False
    limitations: tuple[Text, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def honest_calls(self) -> DemoProvenance:
        if self.preparation == "scripted-replay" and self.provider_calls != 0:
            raise ValueError("scripted replay cannot claim live provider calls")
        return self


class ClaimSupport(StrictModel):
    claim: Text
    source_ids: tuple[Identity, ...] = Field(min_length=1)
    locator: Text
    classification: Literal["record", "allegation", "disputed", "law", "hypothetical", "outcome"]


class DemoStage(StrictModel):
    kind: Literal["initial", "critique", "revised", "assessment"]
    title: Text
    text: Text
    turn_ids: tuple[Text, ...] = Field(min_length=1)


class DemoGateState(StrictModel):
    run_status: Text
    export_ready: bool
    blockers: tuple[Text, ...]
    results: dict[str, Literal["pass", "fail", "not_evaluated"]]


class DemoStrategy(StrictModel):
    strategy_id: Identity
    title: Text
    run_id: Identity
    input_summary: Text
    assumptions: tuple[Text, ...] = ()
    stages: tuple[DemoStage, ...] = Field(min_length=4)
    gate_state: DemoGateState
    source_ids: tuple[Identity, ...] = ()

    @model_validator(mode="after")
    def complete_stages(self) -> DemoStrategy:
        if sorted(s.kind for s in self.stages) != ["assessment", "critique", "initial", "revised"]:
            raise ValueError("strategy requires exactly one of each work-product stage")
        return self


class DemoSnapshot(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    descriptor: DemoDescriptor
    revision: Identity
    provenance: DemoProvenance
    strategies: tuple[DemoStrategy, ...] = Field(min_length=1)
    sources: tuple[SourceReference, ...] = ()
    claims: tuple[ClaimSupport, ...] = ()
    comparison: Text | None = None
    actual_outcome: Text | None = None
    outcome_source_ids: tuple[Identity, ...] = ()
    local_instructions: Text
    bundle_sha256: Digest

    @model_validator(mode="after")
    def cross_references(self) -> DemoSnapshot:
        sources = {s.source_id: s for s in self.sources}
        if len(sources) != len(self.sources):
            raise ValueError("duplicate source identity")
        if len({s.strategy_id for s in self.strategies}) != len(self.strategies):
            raise ValueError("duplicate strategy identity")
        for strategy in self.strategies:
            if not set(strategy.source_ids) <= sources.keys():
                raise ValueError("unknown strategy source")
            if self.descriptor.cutoff is not None and any(
                not sources[key].eligible_at(self.descriptor.cutoff) for key in strategy.source_ids
            ):
                raise ValueError("strategy source is not eligible at cutoff")
        for claim in self.claims:
            if not set(claim.source_ids) <= sources.keys():
                raise ValueError("claim references unknown source")
            support = [sources[key] for key in claim.source_ids]
            if claim.classification == "outcome" and any(
                item.classification != "outcome" for item in support
            ):
                raise ValueError("outcome claim requires outcome sources")
            if claim.classification == "record" and not any(
                item.classification == "record" for item in support
            ):
                raise ValueError("record claim requires record support, not only party allegations")
            if claim.classification != "outcome" and any(
                sources[key].classification == "outcome" for key in claim.source_ids
            ):
                raise ValueError("actual outcome cannot support an ordinary input claim")
            if (
                claim.classification not in {"outcome", "hypothetical"}
                and self.descriptor.cutoff is not None
                and any(not item.eligible_at(self.descriptor.cutoff) for item in support)
            ):
                raise ValueError("claim source is not eligible at cutoff")
        if any(
            key not in sources or sources[key].classification != "outcome"
            for key in self.outcome_source_ids
        ):
            raise ValueError("outcome references must name outcome sources")
        if self.descriptor.collection == "public-record":
            if len(self.strategies) < 2 or not self.comparison or not self.actual_outcome:
                raise ValueError("real case needs two strategies, comparison and actual outcome")
            if (
                not self.claims
                or not self.outcome_source_ids
                or any(not s.source_ids for s in self.strategies)
            ):
                raise ValueError("real case needs material-claim and strategy source support")
        return self


class BundleFile(StrictModel):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{0,127}$")
    text: str = Field(min_length=1)
    sha256: Digest


class LocalInputBundle(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    demo_id: Identity
    revision: Identity
    task: DemoTask
    software_revision: Text
    instructions: Text
    files: tuple[BundleFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_files(self) -> LocalInputBundle:
        import hashlib

        if len({f.name for f in self.files}) != len(self.files):
            raise ValueError("duplicate bundle filename")
        for entry in self.files:
            if hashlib.sha256(entry.text.encode()).hexdigest() != entry.sha256:
                raise ValueError("bundle file digest mismatch")
        return self


class PublicationReview(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    demo_id: Identity
    revision: Identity
    snapshot_sha256: Digest
    bundle_sha256: Digest
    reviewer_kind: Literal["human-editor", "agent-editor"]
    source_support_review: Text
    publication_eligible: Literal[True]
    legal_approval: Literal[False] = False


class CatalogEntry(StrictModel):
    descriptor: DemoDescriptor
    revision: Identity
    snapshot_sha256: Digest
    bundle_sha256: Digest
    review_sha256: Digest


class DemoCatalog(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    release_id: Identity
    legacy_sha256: Digest | None = None
    entries: tuple[CatalogEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_demos(self) -> DemoCatalog:
        if len({e.descriptor.demo_id for e in self.entries}) != len(self.entries):
            raise ValueError("duplicate demo identity")
        return self


class LegacyProjection(VersionedModel):
    """Enumerated API responses from the original fictional demonstration."""

    schema_version: Literal["1.0"] = "1.0"
    responses: dict[str, JsonValue]
    deliverables: dict[str, str]

    @model_validator(mode="after")
    def required_views(self) -> LegacyProjection:
        if (
            not {"matter", "run", "requests", "decisions", "gates", "deliverables", "sets"}
            <= self.responses.keys()
        ):
            raise ValueError("missing original demo views")
        for name in self.deliverables:
            if (
                name.startswith("/")
                or "\\" in name
                or any(p in ("", ".", "..") for p in name.split("/"))
                or not name.endswith((".md", ".json"))
            ):
                raise ValueError("invalid public deliverable name")
        return self


class DemoReleasePin(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    release_id: Identity
    sha256: Digest
    url: str = Field(
        pattern=r"^https://github\.com/damienriehl/mootloop/releases/download/[a-z0-9._-]+/release\.tar$"
    )
