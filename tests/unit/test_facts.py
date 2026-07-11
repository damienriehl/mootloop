"""Unit tests for the append-only fact repository and its fold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mootloop.errors import FactError
from mootloop.facts import FactStore, add_facts_from_file, fold
from mootloop.ingest import ingest_folder
from mootloop.models.common import DocId
from mootloop.models.facts import Fact, Provenance
from mootloop.vault import create_vault
from tests.conftest import make_matter

NOW = "2026-07-11T12:00:00+00:00"


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    create_vault(root, make_matter(), registry_path=tmp_path / "canaries.json")
    return root


def _prov(doc: str = "doc-aaaabbbbccccdddd", quote: str = "q") -> Provenance:
    return Provenance(doc_id=DocId(doc), quote=quote)


def test_add_and_get_current(tmp_path: Path) -> None:
    store = FactStore(_vault(tmp_path))
    fact = store.add_fact("The contract was signed on Jan 3.", provenance=[_prov()], confidence=0.9)
    assert fact.version == 1
    assert fact.superseded_by is None
    current = store.get_current()
    assert len(current) == 1
    assert current[0].fact_id == fact.fact_id


def test_empty_provenance_allowed(tmp_path: Path) -> None:
    store = FactStore(_vault(tmp_path))
    fact = store.add_fact("Unsupported assertion.", confidence=0.3)
    assert fact.provenance == []
    assert store.get_current()[0].provenance == []


def test_confidence_bounds_enforced(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        Fact(fact_id=DocId("fact-x"), statement="s", confidence=1.5, version=1)  # type: ignore[arg-type]


def test_revise_supersedes_predecessor_without_mutating_log(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    store = FactStore(vault)
    v1 = store.add_fact("Delivery was late.", provenance=[_prov()], confidence=0.6)

    log_after_add = (vault / "facts" / "facts.jsonl").read_text(encoding="utf-8")

    v2 = store.revise_fact(
        v1.fact_id, "Delivery was 12 days late.", provenance=[_prov()], confidence=0.8
    )
    assert v2.version == 2
    assert v2.superseded_by is None

    # get_current shows only the new version
    current = store.get_current()
    assert [f.fact_id for f in current] == [v2.fact_id]

    # both versions remain retrievable
    all_ids = {f.fact_id for f in store.all_folded()}
    assert v1.fact_id in all_ids and v2.fact_id in all_ids
    # predecessor now points to successor (via re-emitted line, not mutation)
    pred = store.get(v1.fact_id)
    assert pred is not None and pred.superseded_by == v2.fact_id

    # the ORIGINAL first line is byte-for-byte still present (append-only)
    full_log = (vault / "facts" / "facts.jsonl").read_text(encoding="utf-8")
    assert full_log.startswith(log_after_add)


def test_revise_unknown_id_raises(tmp_path: Path) -> None:
    store = FactStore(_vault(tmp_path))
    with pytest.raises(FactError):
        store.revise_fact("fact-nope", "x", confidence=0.5)


def test_revise_already_superseded_raises(tmp_path: Path) -> None:
    store = FactStore(_vault(tmp_path))
    v1 = store.add_fact("A.", confidence=0.5)
    store.revise_fact(v1.fact_id, "B.", confidence=0.5)
    with pytest.raises(FactError):
        store.revise_fact(v1.fact_id, "C.", confidence=0.5)


def test_fold_is_pure_last_write_wins() -> None:
    a1 = Fact(fact_id=DocId("fact-a"), statement="a", confidence=0.5, version=1)  # type: ignore[arg-type]
    a2 = Fact(  # re-emission of same id with supersession pointer
        fact_id=DocId("fact-a"),  # type: ignore[arg-type]
        statement="a",
        confidence=0.5,
        version=1,
        superseded_by="fact-b",
    )
    folded = fold([a1, a2])
    assert folded["fact-a"].superseded_by == "fact-b"


def test_add_facts_from_file_resolves_source_to_doc_id(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    src = tmp_path / "src"
    src.mkdir()
    (src / "contract.md").write_text("The price is $50,000.", encoding="utf-8")
    ingest_folder(vault, src, now=NOW)

    facts_json = tmp_path / "facts.json"
    facts_json.write_text(
        json.dumps(
            [
                {
                    "statement": "The contract price was $50,000.",
                    "confidence": 0.95,
                    "provenance": [
                        {"source": "contract.md", "quote": "The price is $50,000.",
                         "location_hint": "line 1"}
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    added = add_facts_from_file(vault, facts_json)
    assert len(added) == 1
    prov = added[0].provenance[0]
    assert prov.doc_id.startswith("doc-")
    assert prov.quote == "The price is $50,000."


def test_add_facts_from_file_unknown_source_raises(tmp_path: Path) -> None:
    vault = _vault(tmp_path)
    facts_json = tmp_path / "facts.json"
    facts_json.write_text(
        json.dumps([{"statement": "x", "provenance": [{"source": "missing.md", "quote": "q"}]}]),
        encoding="utf-8",
    )
    with pytest.raises(FactError):
        add_facts_from_file(vault, facts_json)
