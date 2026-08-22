"""The ``needs_attention`` reopen verb: blocker derivation, the gated transition
(counter-capped and driver-halted), and the attempt grant.

Everything here runs against a synthetic single-request vault built in ``tmp_path``
(the same shape ``test_run_pause.py`` uses) — never a real matter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from mootloop.errors import OrchestratorError
from mootloop.journal import append, load_state, read_events
from mootloop.models.common import DocId
from mootloop.models.events import JournalEvent, RunFinished, RunReopened
from mootloop.models.evidence import RunStatusSidecar
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.models.run import DiscardedTurn
from mootloop.orchestrator import (
    attention_blockers,
    effective_max_attempts,
    plan_next,
    record_turn,
    reopen_run,
    start_run,
    status_summary,
)

NOW = "2026-07-11T00:00:00+00:00"
MAX_ATTEMPTS = 3


def _build_vault(tmp_path: Path) -> Path:
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore
    from mootloop.vault import init_vault
    from tests.conftest import make_matter

    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    save_requests(
        vault,
        RequestSet(
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
        ),
    )
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    return vault


def _burn_attempts(vault: Path, run_id: str, *, times: int = MAX_ATTEMPTS) -> str:
    """Derail the first schedulable turn until the counter cap halts the run."""
    turn_id = plan_next(vault, run_id, max_attempts=MAX_ATTEMPTS)[0].turn_id
    for _ in range(times):
        result = record_turn(
            vault, run_id, turn_id, "not valid json", None, NOW, max_attempts=MAX_ATTEMPTS
        )
        assert isinstance(result, DiscardedTurn)
    return turn_id


def _counter_capped_run(tmp_path: Path, run_id: str) -> tuple[Path, str]:
    vault = _build_vault(tmp_path)
    start_run(vault, "discovery-responses", NOW, run_id=run_id, max_attempts=MAX_ATTEMPTS)
    turn_id = _burn_attempts(vault, run_id)
    assert load_state(vault, run_id).status == "needs_attention"
    return vault, turn_id


# --- blocker derivation -----------------------------------------------------


def test_counter_capped_turn_is_a_blocker(tmp_path: Path) -> None:
    vault, turn_id = _counter_capped_run(tmp_path, "reopen-0001")

    blockers = attention_blockers(vault, "reopen-0001", max_attempts=MAX_ATTEMPTS)
    assert [b.ref for b in blockers] == [turn_id]
    assert blockers[0].kind == "counter_capped_turn"
    # The status snapshot surfaces the same list, so the skill loop can report it.
    summary = status_summary(vault, "reopen-0001")
    assert [b["ref"] for b in summary["attention_blockers"]] == [turn_id]  # type: ignore[index,union-attr]


def test_a_prospective_grant_clears_the_blocker(tmp_path: Path) -> None:
    vault, _ = _counter_capped_run(tmp_path, "reopen-0002")
    # Same journal, higher ceiling: the "would this grant unblock it?" question.
    assert attention_blockers(vault, "reopen-0002", max_attempts=MAX_ATTEMPTS) != []
    assert (
        attention_blockers(vault, "reopen-0002", max_attempts=MAX_ATTEMPTS, grant_attempts=2) == []
    )


def test_driver_halted_run_has_no_blockers(tmp_path: Path) -> None:
    """An auth/provider halt (the worker's ``needs_attention``) leaves nothing in the
    run to clear — the operator's logged reason is the whole gate."""
    vault = _build_vault(tmp_path)
    start_run(vault, "discovery-responses", NOW, run_id="reopen-0003")
    append(vault, "reopen-0003", RunFinished(status="needs_attention"))

    assert attention_blockers(vault, "reopen-0003") == []
    state = reopen_run(vault, "reopen-0003", reason="rotated the provider credential")
    assert state.status == "running"
    assert plan_next(vault, "reopen-0003")  # schedulable again


def test_observed_status_sidecar_tracks_reopen(tmp_path: Path) -> None:
    vault = _build_vault(tmp_path)
    run_id = start_run(
        vault, "discovery-responses", NOW, run_id="reopen-observed", mode="observed"
    )
    append(vault, run_id, RunFinished(status="needs_attention"))

    reopen_run(vault, run_id, reason="restored provider access")

    sidecar = RunStatusSidecar.model_validate_json(
        (vault / "runs" / run_id / "STATUS.json").read_text(encoding="utf-8")
    )
    assert sidecar.status == "running"


# --- the transition ---------------------------------------------------------


def test_reopen_refuses_while_blockers_stand(tmp_path: Path) -> None:
    vault, turn_id = _counter_capped_run(tmp_path, "reopen-0004")

    with pytest.raises(OrchestratorError) as excinfo:
        reopen_run(vault, "reopen-0004", reason="fixed the persona body")
    assert turn_id in str(excinfo.value)  # the message names what to fix
    assert load_state(vault, "reopen-0004").status == "needs_attention"  # unchanged
    assert plan_next(vault, "reopen-0004") == []


def test_grant_reopens_and_restores_retry_budget(tmp_path: Path) -> None:
    vault, turn_id = _counter_capped_run(tmp_path, "reopen-0005")

    state = reopen_run(
        vault,
        "reopen-0005",
        reason="tightened the schema instructions in the persona body",
        grant_attempts=2,
        max_attempts=MAX_ATTEMPTS,
    )
    assert state.status == "running"
    assert state.attempts_granted == 2
    assert effective_max_attempts(state, MAX_ATTEMPTS) == 5
    assert [s.turn_id for s in plan_next(vault, "reopen-0005", max_attempts=MAX_ATTEMPTS)] == [
        turn_id
    ]

    # The grant is real budget, not a one-tick reprieve: the next discard does NOT
    # immediately re-block the run, and the one after that (ceiling 5) does.
    assert isinstance(
        record_turn(
            vault, "reopen-0005", turn_id, "still bad", None, NOW, max_attempts=MAX_ATTEMPTS
        ),
        DiscardedTurn,
    )
    assert load_state(vault, "reopen-0005").status == "running"
    record_turn(vault, "reopen-0005", turn_id, "still bad", None, NOW, max_attempts=MAX_ATTEMPTS)
    assert load_state(vault, "reopen-0005").status == "needs_attention"


def test_new_reopen_events_keep_legacy_forced_field_false(tmp_path: Path) -> None:
    """The persisted compatibility field is never advertised or set by new calls."""
    vault = _build_vault(tmp_path)
    start_run(vault, "discovery-responses", NOW, run_id="reopen-0007")
    append(vault, "reopen-0007", RunFinished(status="needs_attention"))

    reopen_run(vault, "reopen-0007", reason="auth restored")
    event = next(e for e in read_events(vault, "reopen-0007") if isinstance(e, RunReopened))
    assert event.forced is False


# --- preconditions ----------------------------------------------------------


@pytest.mark.parametrize("status", ["running", "finished", "capped", "paused"])
def test_reopen_rejects_a_run_that_is_not_needs_attention(tmp_path: Path, status: str) -> None:
    vault = _build_vault(tmp_path)
    run_id = f"reopen-{status}"
    start_run(vault, "discovery-responses", NOW, run_id=run_id)
    if status != "running":
        append(vault, run_id, RunFinished(status=status))  # type: ignore[arg-type]

    with pytest.raises(OrchestratorError, match="not blocked on attention"):
        reopen_run(vault, run_id, reason="wishful thinking")
    assert load_state(vault, run_id).status == status


def test_reopen_requires_a_reason(tmp_path: Path) -> None:
    vault, _ = _counter_capped_run(tmp_path, "reopen-0008")
    with pytest.raises(OrchestratorError, match="non-empty reason"):
        reopen_run(vault, "reopen-0008", reason="   ")
    with pytest.raises(OrchestratorError, match="grant_attempts"):
        reopen_run(vault, "reopen-0008", reason="ok", grant_attempts=-1)
    assert load_state(vault, "reopen-0008").status == "needs_attention"
    assert [e for e in read_events(vault, "reopen-0008") if isinstance(e, RunReopened)] == []


def test_reopen_is_replayable_from_the_journal_alone(tmp_path: Path) -> None:
    """State is always the fold: a re-read of the journal shows the reopened run and
    the ceiling it now runs under (no hand-edited state, ever)."""
    vault, _ = _counter_capped_run(tmp_path, "reopen-0009")
    reopen_run(vault, "reopen-0009", reason="persona fix shipped", grant_attempts=1)

    from mootloop.journal import fold

    replayed = fold(read_events(vault, "reopen-0009"))
    assert replayed.status == "running"
    assert replayed.attempts_granted == 1
    assert not replayed.is_terminal


@pytest.mark.parametrize(
    ("reason", "grant_attempts"),
    [("", 0), ("   ", 0), ("fixed", -1)],
)
def test_run_reopened_rejects_invalid_audit_fields(
    reason: str, grant_attempts: int
) -> None:
    with pytest.raises(ValidationError):
        RunReopened(reason=reason, grant_attempts=grant_attempts)


def test_journal_parser_rejects_invalid_run_reopened_event() -> None:
    adapter: TypeAdapter[JournalEvent] = TypeAdapter(JournalEvent)
    with pytest.raises(ValidationError):
        adapter.validate_python(
            {
                "kind": "run_reopened",
                "reason": "   ",
                "grant_attempts": -1,
                "reopened_by": "operator",
            }
        )


def test_run_reopened_strips_reason() -> None:
    assert RunReopened(reason="  credentials rotated  ").reason == "credentials rotated"
