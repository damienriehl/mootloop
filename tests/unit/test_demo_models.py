from datetime import date

import pytest
from pydantic import ValidationError

from mootloop.models.demo import DemoDescriptor, SourceReference


def source(**changes):
    values = {
        "source_id": "brief",
        "title": "Public brief",
        "url": "https://example.org/brief.pdf",
        "available_on": date(2020, 1, 1),
        "retrieved_on": date(2026, 9, 20),
        "sha256": "a" * 64,
        "locator": "printed page 4",
        "classification": "allegation",
        "redistribution": "link-only",
        "redistribution_basis": "Publicly linked; no copy licensed",
    }
    return SourceReference(**(values | changes))


def test_source_distinguishes_outcome_from_eligible_record():
    assert source().eligible_at(date(2020, 2, 1))
    assert not source(classification="outcome").eligible_at(date(2020, 2, 1))
    assert not source(available_on=date(2021, 1, 1)).eligible_at(date(2020, 2, 1))
    assert not source(available_on=None).eligible_at(date(2020, 2, 1))


@pytest.mark.parametrize(
    "changes",
    [
        {"url": "javascript:alert(1)"},
        {"url": "https://user:secret@example.org/"},
        {"locator": " "},
        {"redistribution_basis": ""},
        {"schema_version": "9.0"},
    ],
)
def test_source_rejects_unsafe_or_unaccountable_metadata(changes):
    with pytest.raises(ValidationError):
        source(**changes)


def test_descriptor_round_trip_and_identity():
    demo = DemoDescriptor(
        demo_id="employment-retaliation",
        title="Employment retaliation",
        introduction="A fictional employee challenges a retaliatory dismissal.",
        collection="synthetic",
        practice_area="Employment",
        work_product="Complaint",
        jurisdiction="New York",
        court_level="state-trial",
        task="complaint",
    )
    assert DemoDescriptor.model_validate_json(demo.model_dump_json()) == demo
    with pytest.raises(ValidationError):
        DemoDescriptor.model_validate(demo.model_dump() | {"demo_id": "../secrets"})


def test_real_case_requires_historical_cutoff():
    with pytest.raises(ValidationError, match="cutoff"):
        DemoDescriptor(
            demo_id="example",
            title="Example",
            introduction="An historical exercise.",
            collection="public-record",
            practice_area="Contracts",
            work_product="Motion",
            jurisdiction="Federal",
            court_level="federal-trial",
            task="motion",
        )


def test_committed_collection_has_all_twenty_distinct_descriptors():
    from collections import Counter
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "config/demos/catalog.yaml").read_text())
    demos = [DemoDescriptor.model_validate(row) for row in raw["entries"]]
    assert len({demo.demo_id for demo in demos}) == 20
    assert Counter(demo.collection for demo in demos) == {
        "synthetic": 5,
        "public-record": 5,
        "business": 10,
    }


def _snapshot_with_claim(claim_classification, claim_source):
    from mootloop.models.demo import DemoSnapshot

    eligible = source(source_id="record", classification="record")
    strategy = {
        "strategy_id": "strategy-a",
        "title": "A narrower request",
        "run_id": "prepared-a",
        "input_summary": "A bounded historical record.",
        "assumptions": ["Hypothetical change in emphasis only."],
        "stages": [
            {"kind": kind, "title": kind, "text": "Prepared review text.", "turn_ids": [kind]}
            for kind in ("initial", "critique", "revised", "assessment")
        ],
        "gate_state": {
            "run_status": "finished",
            "export_ready": False,
            "blockers": ["attestation"],
            "results": {"rubric": "pass"},
        },
        "source_ids": ["record"],
    }
    return DemoSnapshot.model_validate(
        {
            "descriptor": {
                "demo_id": "example",
                "title": "Example",
                "introduction": "Historical exercise.",
                "collection": "public-record",
                "practice_area": "Contracts",
                "work_product": "Motion",
                "jurisdiction": "Federal",
                "court_level": "federal-trial",
                "task": "motion",
                "cutoff": "2020-02-01",
                "represented_side": "Applicant",
            },
            "revision": "r1",
            "provenance": {
                "preparation": "scripted-replay",
                "authorship": "Agent editor",
                "editorial_changes": "Prepared example",
                "provider_calls": 0,
                "limitations": ["No attorney approval"],
            },
            "strategies": [
                strategy,
                strategy | {"strategy_id": "strategy-b", "run_id": "prepared-b"},
            ],
            "sources": [
                eligible,
                claim_source,
                source(source_id="outcome", classification="outcome"),
            ],
            "claims": [
                {
                    "claim": "A material proposition",
                    "source_ids": [claim_source.source_id],
                    "locator": "Page 4",
                    "classification": claim_classification,
                }
            ],
            "comparison": "Neither alternative guarantees relief.",
            "actual_outcome": "The court denied relief.",
            "outcome_source_ids": ["outcome"],
            "local_instructions": "Import the permitted inputs.",
            "bundle_sha256": "b" * 64,
        }
    )


@pytest.mark.parametrize("available_on", [None, date(2020, 3, 1)])
def test_ordinary_claim_cannot_launder_ineligible_source(available_on):
    with pytest.raises(ValidationError, match="claim source is not eligible"):
        _snapshot_with_claim("allegation", source(available_on=available_on))


def test_contested_brief_cannot_be_only_support_for_record_fact():
    with pytest.raises(ValidationError, match="record claim requires"):
        _snapshot_with_claim("record", source(classification="allegation"))


def test_outcome_claim_requires_outcome_source():
    with pytest.raises(ValidationError, match="outcome claim requires"):
        _snapshot_with_claim("outcome", source())


def test_attributed_pre_cutoff_allegation_remains_publishable():
    snapshot = _snapshot_with_claim("allegation", source())
    assert snapshot.claims[0].classification == "allegation"
