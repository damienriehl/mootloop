"""Full pipeline over the synthetic matter via FakeLLMProvider, plus kill-resume.

Exercises the whole stepwise machine end-to-end: every served request drives a
per-request fan-out to a deliverable, a kill mid-run resumes from the journal fold
without re-executing any completed turn, and turn bodies are write-once.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.facts import add_facts_from_file
from mootloop.ingest import ingest_folder
from mootloop.journal import load_state, turn_body_path
from mootloop.llm import FakeLLMProvider, RawTurnResult
from mootloop.models.common import DocId
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestType
from mootloop.orchestrator import (
    load_request_units,
    plan_next,
    record_turn,
    run_with_provider,
    start_run,
)
from mootloop.stages import render_prompt
from mootloop.vault import init_vault
from tests.conftest import resolve_all_decisions

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "fixtures" / "synthetic-matter"
NOW = "2026-07-11T00:00:00+00:00"

_SETS = [
    ("rogs-set1.txt", RequestType.INTERROGATORY),
    ("rfps-set1.txt", RequestType.RFP),
    ("rfas-set1.txt", RequestType.RFA),
]


def _build_matter_vault(tmp_path: Path) -> Path:
    matter = MatterConfig.model_validate(
        yaml.safe_load((FIXTURE / "matter.yaml").read_text(encoding="utf-8"))
    )
    vault = tmp_path / "vault"
    init_vault(vault, matter, registry_path=tmp_path / "canaries.json")
    ingest_folder(vault, FIXTURE / "source-docs", now=NOW, tags_file=FIXTURE / "tags.yaml")
    add_facts_from_file(vault, FIXTURE / "facts.json")
    for filename, request_type in _SETS:
        data = (FIXTURE / "served" / filename).read_bytes()
        report = parse_discovery_document(
            data.decode("utf-8"), request_type, DocId("doc-servedservedserv")
        )
        save_requests(vault, report.request_set)
    return vault


def _response_anchors(deliverable: Path) -> list[str]:
    return re.findall(r"::: \{#resp-([^}]+)\}", deliverable.read_text(encoding="utf-8"))


def test_full_pipeline_produces_one_block_per_request(tmp_path: Path) -> None:
    vault = _build_matter_vault(tmp_path)
    units = load_request_units(vault)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="matter-0001")

    # The default fake proposes an RFA disposition per RFA — a hard-human gate that
    # holds the run at needs_decisions until the attorney resolves it (Phase 5).
    state = run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    assert state.status == "needs_decisions"
    resolve_all_decisions(vault, run_id, NOW)
    assert load_state(vault, run_id).status == "finished"

    deliverable = vault / "deliverables" / "draft-discovery-responses.md"
    assert deliverable.is_file()
    anchors = _response_anchors(deliverable)
    assert len(anchors) == len(units)
    assert set(anchors) == {u.request_id for u in units}


def test_kill_resume_never_reexecutes_completed_turns(tmp_path: Path) -> None:
    vault = _build_matter_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="matter-0002")

    # --- partial run: execute only the first 5 planned turns, then "crash" ---
    provider_a = FakeLLMProvider()
    executed = 0
    while executed < 5:
        specs = plan_next(vault, run_id)
        if not specs:
            break
        for spec in specs:
            if executed >= 5:
                break
            result: RawTurnResult = provider_a.run_turn(spec, render_prompt(spec))
            record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
            executed += 1
    completed_before = set(load_state(vault, run_id).completed_turns)
    assert 0 < len(completed_before) < 100

    # --- resume with a FRESH provider: only new turns may be executed ---
    provider_b = FakeLLMProvider()
    state = run_with_provider(vault, run_id, provider_b, NOW)
    assert state.status == "needs_decisions"
    resolve_all_decisions(vault, run_id, NOW)
    state = load_state(vault, run_id)
    assert state.status == "finished"

    # The resumed provider was never asked to redo an already-completed turn.
    assert not (set(provider_b.calls) & completed_before)
    # And every completed-before turn is still present (nothing was re-run/clobbered).
    assert completed_before <= set(state.completed_turns)

    anchors = _response_anchors(vault / "deliverables" / "draft-discovery-responses.md")
    assert len(anchors) == len(load_request_units(vault))


def test_turn_body_written_once(tmp_path: Path) -> None:
    vault = _build_matter_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="matter-0003")
    provider = FakeLLMProvider()

    spec = plan_next(vault, run_id)[0]
    result = provider.run_turn(spec, render_prompt(spec))
    record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)

    body = turn_body_path(vault, run_id, spec.turn_id)
    assert body.is_file()
    original = body.read_text(encoding="utf-8")
    # Re-recording is idempotent and does not rewrite the body.
    record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
    assert body.read_text(encoding="utf-8") == original
