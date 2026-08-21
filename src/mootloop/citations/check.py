"""Least-privilege cite-checker planning, validation, ledgering, and gate folding."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from mootloop.citations.propositions import PropositionLedger
from mootloop.errors import CitationError
from mootloop.models.citations import (
    AuthorityPassage,
    CitationProposition,
    OpinionAuthorityStoreRecord,
    PropositionVerificationRecord,
    PropositionVerificationStatus,
)
from mootloop.models.common import RunId, StrictModel, TurnId
from mootloop.models.gates import GateFail, GateFinding, GatePass, GatePending, GateResult
from mootloop.models.run import (
    SCHEMA_CITE_CHECK,
    CiteCheckOutput,
    PersonaName,
    TurnSpec,
)

GATE_NAME = "citation_propositions"


class PropositionEvidenceBundle(StrictModel):
    """The complete bounded DATA payload for one exact proposition check."""

    run_id: RunId
    proposition: CitationProposition
    authority: OpinionAuthorityStoreRecord
    passages: list[AuthorityPassage]


def build_check_spec(
    run_id: str,
    bundle: PropositionEvidenceBundle,
    *,
    model: str | None,
) -> TurnSpec:
    """Build a stable cite-checker turn with no tools and only bounded public DATA."""
    suffix = str(bundle.proposition.proposition_id).removeprefix("prop-")
    return TurnSpec(
        turn_id=TurnId(f"{run_id}-cite-{suffix}"),
        run_id=RunId(run_id),
        persona=PersonaName.CITE_CHECKER,
        stage="citation_proposition",
        prompt_context={
            "directive": (
                "Determine whether the attributed proposition is supported by the supplied "
                "authority passages. Do not use outside knowledge. Cite only supplied passage IDs."
            ),
            "proposition": bundle.proposition.model_dump(mode="json"),
            "authority": {
                "citation_id": bundle.authority.citation_id,
                "cluster_id": bundle.authority.cluster_id,
                "source_url": bundle.authority.source_url,
                "content_sha256": bundle.authority.content_sha256,
            },
            "passages": [passage.model_dump(mode="json") for passage in bundle.passages],
        },
        output_schema_name=SCHEMA_CITE_CHECK,
        model=model,
    )


def record_check_result(
    vault_root: Path | str,
    bundle: PropositionEvidenceBundle,
    raw_text: str,
    *,
    checked_at: str,
) -> PropositionVerificationRecord:
    """Validate one untrusted model result and append its exact-authority verdict."""
    _, record = validate_check_result(bundle, raw_text, checked_at=checked_at)
    PropositionLedger(vault_root).append(record)
    return record


def validate_check_result(
    bundle: PropositionEvidenceBundle,
    raw_text: str,
    *,
    checked_at: str,
) -> tuple[CiteCheckOutput, PropositionVerificationRecord]:
    """Validate untrusted output without writing, for journal-before-view ordering."""
    try:
        output = CiteCheckOutput.model_validate_json(raw_text)
    except ValidationError as exc:
        raise CitationError(
            f"cite-checker output is invalid: {exc.error_count()} error(s)"
        ) from exc
    allowed = {passage.passage_id for passage in bundle.passages}
    selected = set(output.evidence_passage_ids)
    unknown = selected - allowed
    if unknown:
        raise CitationError(f"cite-checker selected unknown evidence passage: {sorted(unknown)[0]}")
    if output.status == "supported" and not selected:
        raise CitationError("supported cite-checker result requires supplied evidence")
    record = PropositionVerificationRecord(
        run_id=bundle.run_id,
        proposition_id=bundle.proposition.proposition_id,
        citation_id=bundle.proposition.citation_id,
        source_text_sha256=bundle.proposition.source_text_sha256,
        status=PropositionVerificationStatus(output.status),
        source="cite_checker",
        checked_at=checked_at,
        authority_sha256=bundle.authority.content_sha256,
        authority_source_url=bundle.authority.source_url,
        evidence_passage_ids=output.evidence_passage_ids,
        notes=output.reasoning,
    )
    return output, record


def proposition_gate(
    vault_root: Path | str,
    bundles: list[PropositionEvidenceBundle],
    *,
    unresolved: list[CitationProposition] | None = None,
) -> GateResult:
    """Fail unsupported/ambiguous claims; leave missing checks pending."""
    ledger = PropositionLedger(vault_root)
    failures: list[GateFinding] = []
    pending: list[GateFinding] = []
    for bundle in bundles:
        proposition = bundle.proposition
        record = ledger.get(
            bundle.run_id,
            proposition.proposition_id,
            proposition.source_text_sha256,
            bundle.authority.content_sha256,
        )
        status = record.status if record else PropositionVerificationStatus.PENDING
        if status == PropositionVerificationStatus.SUPPORTED:
            continue
        finding = GateFinding(
            code=f"citation_proposition_{status.value}",
            message=(
                f"{proposition.normalized_citation!r} is {status.value} for the exact "
                "proposition attributed to it"
            ),
            locator=proposition.proposition_id,
        )
        if status in {
            PropositionVerificationStatus.UNSUPPORTED,
            PropositionVerificationStatus.AMBIGUOUS,
        }:
            failures.append(finding)
        else:
            pending.append(finding)
    for proposition in unresolved or []:
        pending.append(
            GateFinding(
                code="citation_proposition_needs_research",
                message=(
                    f"{proposition.normalized_citation!r} needs an exact authority text "
                    "before proposition support can be checked"
                ),
                locator=proposition.proposition_id,
            )
        )
    if failures:
        return GateFail(gate=GATE_NAME, findings=failures + pending)
    if pending:
        return GatePending(gate=GATE_NAME, findings=pending)
    return GatePass(gate=GATE_NAME)


def proposition_export_gate(
    vault_root: Path | str,
    run_id: str,
    propositions: list[CitationProposition],
) -> GateResult:
    """Read-only export gate over the latest exact-snapshot result per proposition."""
    latest = PropositionLedger(vault_root).latest_by_proposition(run_id)
    failures: list[GateFinding] = []
    pending: list[GateFinding] = []
    for proposition in propositions:
        record = latest.get(
            (proposition.proposition_id, proposition.source_text_sha256)
        )
        status = record.status if record else PropositionVerificationStatus.PENDING
        if status == PropositionVerificationStatus.SUPPORTED:
            continue
        finding = GateFinding(
            code=f"citation_proposition_{status.value}",
            message=(
                f"{proposition.normalized_citation!r} is {status.value} for the exact "
                "proposition attributed to it"
            ),
            locator=proposition.proposition_id,
        )
        if status in {
            PropositionVerificationStatus.UNSUPPORTED,
            PropositionVerificationStatus.AMBIGUOUS,
        }:
            failures.append(finding)
        else:
            pending.append(finding)
    if failures:
        return GateFail(gate=GATE_NAME, findings=failures + pending)
    if pending:
        return GatePending(gate=GATE_NAME, findings=pending)
    return GatePass(gate=GATE_NAME)
