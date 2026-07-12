"""Run pause/resume lifecycle + the FD-6 conservative (write-ahead) budget cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.errors import OrchestratorError
from mootloop.journal import append
from mootloop.models.common import DocId
from mootloop.models.events import RunFinished, TurnIntent
from mootloop.models.matter import MatterConfig
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.orchestrator import pause_run, plan_next, resume_run, status_summary

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
    from mootloop.orchestrator import start_run

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
    from mootloop.orchestrator import start_run

    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0002")
    append(vault, run_id, RunFinished(status="finished"))
    with pytest.raises(OrchestratorError):
        pause_run(vault, run_id)


def test_resume_rejects_non_paused_run(tmp_path: Path) -> None:
    from mootloop.orchestrator import start_run

    vault = _build_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="pause-0003")
    with pytest.raises(OrchestratorError):
        resume_run(vault, run_id)


def test_conservative_cap_counts_unreconciled_intent(tmp_path: Path) -> None:
    from mootloop.orchestrator import start_run

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
