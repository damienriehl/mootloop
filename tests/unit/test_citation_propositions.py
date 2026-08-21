from __future__ import annotations

import hashlib

from mootloop.citations.propositions import (
    PropositionLedger,
    extract_citation_propositions,
)
from mootloop.models.citations import (
    PropositionVerificationRecord,
    PropositionVerificationStatus,
)

NOW = "2026-08-21T16:00:00+00:00"


def test_extract_maps_cleaned_citation_back_to_original_paragraph_offsets() -> None:
    text = (
        "Unrelated opening paragraph.\n\n"
        "  A court requires a particularized response under  Smith v. Jones, 123 F.3d "
        "456 (8th Cir. 2000).  \n\nTrailing paragraph."
    )

    [proposition] = extract_citation_propositions(text, source_turn_id="run-1-t0001")

    assert proposition.proposition_text == (
        "A court requires a particularized response under  Smith v. Jones, 123 F.3d "
        "456 (8th Cir. 2000)."
    )
    assert text[proposition.proposition_start : proposition.proposition_end].strip() == (
        proposition.proposition_text
    )
    assert text[proposition.citation_start : proposition.citation_end] == (
        "Smith v. Jones, 123 F.3d 456 (8th Cir. 2000)"
    )
    assert proposition.source_turn_id == "run-1-t0001"
    assert proposition.source_text_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_proposition_identity_changes_with_the_asserted_proposition() -> None:
    first = extract_citation_propositions(
        "The contract requires notice. Smith v. Jones, 123 F.3d 456 (8th Cir. 2000)."
    )[0]
    second = extract_citation_propositions(
        "The contract bars notice. Smith v. Jones, 123 F.3d 456 (8th Cir. 2000)."
    )[0]

    assert first.citation_id == second.citation_id
    assert first.proposition_id != second.proposition_id


def test_proposition_ledger_is_append_only_and_latest_exact_authority_wins(tmp_path) -> None:
    [proposition] = extract_citation_propositions(
        "Notice is required. Smith v. Jones, 123 F.3d 456 (8th Cir. 2000)."
    )
    ledger = PropositionLedger(tmp_path)
    pending = PropositionVerificationRecord(
        run_id="run-1",
        proposition_id=proposition.proposition_id,
        citation_id=proposition.citation_id,
        source_text_sha256=proposition.source_text_sha256,
        status=PropositionVerificationStatus.PENDING,
        source="cite_checker",
        checked_at=NOW,
        authority_sha256="a" * 64,
        authority_source_url="https://www.courtlistener.com/opinion/1/example/",
    )
    supported = pending.model_copy(
        update={
            "status": PropositionVerificationStatus.SUPPORTED,
            "evidence_passage_ids": ["passage-1"],
        }
    )

    ledger.append(pending)
    ledger.append(supported)

    assert ledger.get(
        "run-1", proposition.proposition_id, proposition.source_text_sha256, "a" * 64
    ) == supported
    assert ledger.get(
        "run-1", proposition.proposition_id, "c" * 64, "a" * 64
    ) is None
    assert ledger.get(
        "run-1", proposition.proposition_id, proposition.source_text_sha256, "b" * 64
    ) is None
    assert ledger.get(
        "run-2", proposition.proposition_id, proposition.source_text_sha256, "a" * 64
    ) is None
    assert len((tmp_path / "law" / "proposition-verifications.jsonl").read_text().splitlines()) == 2
