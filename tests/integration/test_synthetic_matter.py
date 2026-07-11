"""End-to-end integration over the fully synthetic Northfield Widgets matter.

Builds a vault from the fixture's matter.yaml, ingests the source docs with tags,
parses all three served sets, loads the facts, and asserts the whole pipeline hangs
together: doc counts, role/privilege tags, subpart request IDs, and that every fact
provenance quote is actually present in the normalized corpus text it cites.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from mootloop.discovery_parser import parse_discovery_document
from mootloop.facts import add_facts_from_file
from mootloop.ingest import ingest_folder
from mootloop.models.corpus import DocRole, Manifest
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.vault import init_vault

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
NOW = "2026-07-11T00:00:00+00:00"


def _build_vault(tmp_path: Path) -> Path:
    matter = MatterConfig.model_validate(
        yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    )
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    ingest_folder(vault, FIXTURE / "source-docs", now=NOW, tags_file=FIXTURE / "tags.yaml")
    return vault


def test_ingest_counts_and_tags(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    manifest = Manifest.load(vault)
    by_name = {d.original_name: d for d in manifest.docs}

    # six source docs, all normalized cleanly
    assert len(manifest.docs) == 6
    assert all(d.ingest_status == "ok" for d in manifest.docs)

    # tags applied
    assert by_name["complaint.md"].role == DocRole.COMPLAINT
    assert by_name["answer.md"].role == DocRole.ANSWER
    assert by_name["contract.md"].role == DocRole.CLIENT_DOC
    assert by_name["email1.eml"].role == DocRole.CORRESPONDENCE
    # last-matching rule wins: email3 is privileged, others are not
    assert by_name["email3.eml"].privileged is True
    assert by_name["email1.eml"].privileged is False


def test_served_sets_parse_with_subparts(tmp_path: Path) -> None:
    _build_vault(tmp_path)  # not needed for parsing, but exercises the full path

    rog_text = (FIXTURE / "served" / "rogs-set1.txt").read_text(encoding="utf-8")
    rfp_text = (FIXTURE / "served" / "rfps-set1.txt").read_text(encoding="utf-8")
    rfa_text = (FIXTURE / "served" / "rfas-set1.txt").read_text(encoding="utf-8")
    src = "doc-servedservedserv"  # placeholder source id for parsing

    from mootloop.models.common import DocId

    rog = parse_discovery_document(rog_text, RequestType.INTERROGATORY, DocId(src))
    rfp = parse_discovery_document(rfp_text, RequestType.RFP, DocId(src))
    rfa = parse_discovery_document(rfa_text, RequestType.RFA, DocId(src))

    top_rog = [i for i in rog.request_set.items if i.subpart is None]
    assert len(top_rog) == 8
    assert len(rfp.request_set.items) == 5
    assert len(rfa.request_set.items) == 5

    # the compound interrogatory (ROG-5) produced lettered subpart items
    subpart_ids = {i.request_id for i in rog.request_set.items if i.subpart is not None}
    assert "ROG-5(a)" in subpart_ids
    assert {"ROG-5(a)", "ROG-5(b)", "ROG-5(c)"} <= subpart_ids

    # contiguous numbering → no warnings on any set
    assert rog.warnings == [] and rfp.warnings == [] and rfa.warnings == []


def test_facts_load_and_provenance_quotes_present_in_corpus(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    added = add_facts_from_file(vault, FIXTURE / "facts.json")
    assert len(added) == 6

    manifest = Manifest.load(vault)
    docs = {d.doc_id: d for d in manifest.docs}
    for fact in added:
        assert fact.provenance, f"fact {fact.fact_id} has no provenance"
        for prov in fact.provenance:
            doc = docs[prov.doc_id]
            assert doc.normalized_path is not None
            text = (vault / doc.normalized_path).read_text(encoding="utf-8")
            assert prov.quote in text, (
                f"quote {prov.quote!r} not found in normalized {doc.original_name}"
            )
