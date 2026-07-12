"""Run pause/resume lifecycle + the FD-6 conservative (write-ahead) budget cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.errors import OrchestratorError
from mootloop.journal import append, load_state, read_events
from mootloop.models.common import DocId
from mootloop.models.events import (
    RunFinished,
    RunPaused,
    RunResumed,
    SpendRecorded,
    TurnIntent,
)
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.orchestrator import (
    pause_run,
    plan_next,
    resume_run,
    start_run,
    status_summary,
)


def _projected_spend(vault: Path, run_id: str) -> float:
    """Spend the conservative cap check sees: settled spend + unreconciled reservations."""
    state = load_state(vault, run_id)
    return state.total_spend_usd + sum(state.pending_intents.values())


NOW = "2026-07-11T00:00:00+00:00"


def _matter_with_cap(cap: float | None) -> MatterConfig:
    from tests.conftest import make_matter

    base = make_matter().model_dump()
    base["budget"] = {"tier": "moderate", "hard_cap_usd": cap}
    return MatterConfig.model_validate(base)


def _build_vault(tmp_path: Path, *, cap: float | None = None) -> Path:
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore
    from mootloop.vault import init_vault

    vault = tmp_path / "vault"
    init_vault(vault, _matter_with_cap(cap), registry_path=tmp_path / "canaries.json")
    request_set = RequestSet(
        request_type=RequestType.INTERROGATORY,
        set_number=1,
        title="Interrogatories Set 1",
        items=[
            RequestItem(
                request_id="ROG-1",  # type: ignore[arg-type]
                set_number=1,
                number=1,
                text="Identify every person with knowledge of the contract.",
                source_doc=DocId("doc-servedservedserv"),
            )
        ],
    )
    save_requests(vault, request_set)
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    return vault


def test_pause_then_resume_reopens_run(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0001")
    assert plan_next(vault, run_id)  # schedulable while running

    pause_run(vault, run_id, reason="capacity")
    assert status_summary(vault, run_id)["status"] == "paused"
    assert plan_next(vault, run_id) == []  # paused run schedules nothing

    resume_run(vault, run_id)
    assert status_summary(vault, run_id)["status"] == "running"
    assert plan_next(vault, run_id)  # schedulable again


def test_pause_rejects_terminal_run(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0002")
    append(vault, run_id, RunFinished(status="finished"))
    with pytest.raises(OrchestratorError):
        pause_run(vault, run_id)


def test_resume_rejects_non_paused_run(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0003")
    with pytest.raises(OrchestratorError):
        resume_run(vault, run_id)


def test_pause_resume_event_order_and_non_terminal(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0005")

    pause_run(vault, run_id, reason="manual")
    paused = load_state(vault, run_id)
    assert paused.status == "paused"
    assert paused.is_terminal is False  # a paused run is NOT terminally complete
    assert plan_next(vault, run_id) == []

    resume_run(vault, run_id)
    assert load_state(vault, run_id).status == "running"
    assert plan_next(vault, run_id)  # schedulable work returns

    kinds = [type(e) for e in read_events(vault, run_id)]
    assert kinds.index(RunPaused) < kinds.index(RunResumed)  # paused precedes resumed


def test_reconciliation_drops_projected_spend(tmp_path: Path) -> None:
    # A generous cap so the intent below never trips the cap path; this isolates the
    # write-ahead ledger's double-counting-then-release behavior (plan FD-6).
    vault = _build_vault(tmp_path, cap=100.0)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0006")

    append(
        vault,
        run_id,
        TurnIntent(turn_id="t-x", model="fake", billing_mode="subscription", max_plausible_usd=1.6),
    )
    # Unreconciled: the intent counts at its full max-plausible reservation.
    assert load_state(vault, run_id).pending_intents == {"t-x": 1.6}
    assert _projected_spend(vault, run_id) == 1.6

    # The matching SpendRecorded reconciles the intent and books the real (lower) cost:
    # the reservation is released, so projected spend drops (no double count).
    append(
        vault,
        run_id,
        SpendRecorded(
            turn_id="t-x",
            input_tokens=10,
            cache_read=0,
            cache_write=0,
            output_tokens=5,
            model="fake",
            usd_equiv=0.3,
        ),
    )
    assert load_state(vault, run_id).pending_intents == {}
    assert _projected_spend(vault, run_id) == 0.3


def test_conservative_cap_counts_unreconciled_intent(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path, cap=1.0)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0004")
    # A write-ahead intent whose max-plausible cost alone exceeds the cap must
    # gracefully cap the run before any more work is planned (plan FD-6).
    append(
        vault,
        run_id,
        TurnIntent(turn_id="x", model="fake", billing_mode="subscription", max_plausible_usd=5.0),
    )
    assert plan_next(vault, run_id) == []
    assert status_summary(vault, run_id)["status"] == "capped"
