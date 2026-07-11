"""The baked demo vault is a finished, attested, export-ready run — with the full
arc on display: a restructure pass, RFA-disposition decisions, a verified citation,
and every deliverable written. All offline, all deterministic."""

from __future__ import annotations

from pathlib import Path

from mootloop.attest import attestation_state
from mootloop.decisions import DecisionStore
from mootloop.gate_ledger import TURN_GATES, build_ledger, export_ready
from mootloop.journal import load_state
from mootloop.models.decisions import DecisionKind
from mootloop.web.bake import DEMO_ATTORNEY, DEMO_RUN_ID, RESTRUCTURE_REQUEST_IDS


def test_bake_run_finished_and_attested(demo_vault: Path) -> None:
    state = load_state(demo_vault, DEMO_RUN_ID)
    assert state.status == "finished"
    assert state.completed_turns, "the baked run must have completed turns"
    assert attestation_state(demo_vault, DEMO_RUN_ID).status == "valid"


def test_bake_is_export_ready(demo_vault: Path) -> None:
    ready, blockers = export_ready(demo_vault, DEMO_RUN_ID)
    assert ready is True and blockers == []


def test_bake_gate_ledger_all_requests_fully_pass(demo_vault: Path) -> None:
    """Every request's ledger row shows every turn gate PASS — completeness included
    (regression: the canned demo drafts once failed the deterministic presence
    criteria and every row showed completeness=fail)."""
    doc = build_ledger(demo_vault, DEMO_RUN_ID)
    assert doc.gates, "the ledger must have per-request rows"
    for request_id, row in doc.gates.items():
        assert row["completeness"] == "pass", (
            f"{request_id} completeness must pass, got {row['completeness']!r}"
        )
        for gate in TURN_GATES:
            assert row[gate] == "pass", f"{request_id} {gate} must pass, got {row[gate]!r}"
        for run_gate in ("citations", "decisions", "attestation"):
            assert row[run_gate] == "pass", (
                f"{request_id} {run_gate} must pass, got {row[run_gate]!r}"
            )
    assert doc.export_ready is True and doc.blockers == []


def test_bake_triggers_restructure_on_scripted_subset(demo_vault: Path) -> None:
    state = load_state(demo_vault, DEMO_RUN_ID)
    restructured = {
        str(r.spec.request_id)
        for r in state.completed_turns.values()
        if r.spec.stage == "restructure"
    }
    assert restructured == set(RESTRUCTURE_REQUEST_IDS)


def test_bake_resolves_all_decisions_including_rfa(demo_vault: Path) -> None:
    decisions = DecisionStore(demo_vault, DEMO_RUN_ID).list_all()
    assert decisions, "the baked run must derive attorney-gate decisions"
    assert all(d.status != "open" for d in decisions)
    rfa = [d for d in decisions if d.kind is DecisionKind.RFA_DISPOSITION]
    assert rfa, "RFA requests must derive RFA-disposition decisions"
    for decision in decisions:
        assert decision.resolution is not None
        assert decision.resolution.decided_by == DEMO_ATTORNEY
        assert decision.resolution.source == "human"


def test_bake_writes_deliverables_and_derived_views(demo_vault: Path) -> None:
    deliv = demo_vault / "deliverables" / DEMO_RUN_ID
    for name in ("master.md", "verification.md", "privilege-log.md",
                 "strategy-memo.md", "audit-log.json"):
        assert (deliv / name).is_file(), f"missing deliverable {name}"
    assert list((deliv / "sets").glob("*.md")), "per-set masters must exist"
    run_dir = demo_vault / "runs" / DEMO_RUN_ID
    assert (run_dir / "gate-ledger.json").is_file()
    assert (run_dir / "scores" / "panels" / "report.json").is_file()


def test_bake_master_carries_the_full_document_shape(demo_vault: Path) -> None:
    master = (demo_vault / "deliverables" / DEMO_RUN_ID / "master.md").read_text(
        encoding="utf-8"
    )
    assert "INTERROGATORY NO. 1:" in master
    assert "::: {#resp-ROG-1}" in master
    assert "subject to and without waiving" not in master.lower()
