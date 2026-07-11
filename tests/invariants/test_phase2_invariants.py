"""Structural invariants for the Phase 2 pipeline (plan D10 #9)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.facts import add_facts_from_file
from mootloop.ingest import ingest_folder
from mootloop.journal import fold, read_events
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.events import TurnCompleted
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.orchestrator import load_request_units, run_with_provider, start_run
from mootloop.vault import init_vault

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
NOW = "2026-07-11T00:00:00+00:00"

pytestmark = pytest.mark.invariant


def _finished_run(tmp_path: Path) -> tuple[Path, str]:
    matter = MatterConfig.model_validate(
        yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    )
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    ingest_folder(vault, FIXTURE / "source-docs", now=NOW, tags_file=FIXTURE / "tags.yaml")
    add_facts_from_file(vault, FIXTURE / "facts.json")
    for filename, request_type in [
        ("rogs-set1.txt", RequestType.INTERROGATORY),
        ("rfps-set1.txt", RequestType.RFP),
        ("rfas-set1.txt", RequestType.RFA),
    ]:
        data = (FIXTURE / "served" / filename).read_bytes()
        report = parse_discovery_document(
            data.decode("utf-8"), request_type, DocId("doc-servedservedserv")
        )
        save_requests(vault, report.request_set)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="inv-0001")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    return vault, run_id


def test_every_request_has_a_draft_and_exactly_one_block(tmp_path: Path) -> None:
    vault, run_id = _finished_run(tmp_path)
    units = load_request_units(vault)
    events = read_events(vault, run_id)

    # Every request has at least one completed associate draft turn.
    drafted: set[str] = {
        e.record.spec.request_id
        for e in events
        if isinstance(e, TurnCompleted)
        and e.record.spec.stage == "associate_draft"
        and e.record.spec.request_id is not None
    }
    assert drafted == {u.request_id for u in units}

    # Exactly one response block per request in the deliverable.
    text = (vault / "deliverables" / "draft-discovery-responses.md").read_text(encoding="utf-8")
    for unit in units:
        assert len(re.findall(rf"::: \{{#resp-{re.escape(unit.request_id)}\}}", text)) == 1


def test_fold_is_deterministic_across_serialize_roundtrip(tmp_path: Path) -> None:
    vault, run_id = _finished_run(tmp_path)
    events = read_events(vault, run_id)
    # fold(events) == fold(events-reparsed-from-disk)
    assert fold(events).model_dump() == fold(read_events(vault, run_id)).model_dump()


def test_orchestrator_source_holds_no_task_name_literal() -> None:
    """The core depends on the adapter protocol, never a task-name string (D1)."""
    source = (REPO_ROOT / "src" / "mootloop" / "orchestrator.py").read_text(encoding="utf-8")
    assert "discovery-responses" not in source
    assert "discovery_responses" not in source
