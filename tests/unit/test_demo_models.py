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
