"""plan_next stage-order + loop-cap logic and the derailment/discard contract."""

from __future__ import annotations

from pathlib import Path

from mootloop.facts import FactStore
from mootloop.llm import FakeLLMProvider, RawTurnResult
from mootloop.models.common import DocId
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.models.run import DiscardedTurn, PersonaName
from mootloop.orchestrator import (
    plan_next,
    record_turn,
    start_run,
    status_summary,
)
from mootloop.stages import render_prompt

NOW = "2026-07-11T00:00:00+00:00"


def _build_single_request_vault(tmp_path: Path) -> Path:
    from mootloop.vault import init_vault
    from tests.conftest import make_matter

    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
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
    from mootloop.discovery_parser import save_requests

    save_requests(vault, request_set)
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    return vault


def _run_step(vault: Path, run_id: str, provider: FakeLLMProvider) -> list[str]:
    """Execute one plan_next batch; return the personas that ran, in order."""
    specs = plan_next(vault, run_id)
    ran: list[str] = []
    for spec in specs:
        result: RawTurnResult = provider.run_turn(spec, render_prompt(spec))
        record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
        ran.append(spec.persona.value)
    return ran


def test_stage_order_single_request(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-0001")
    provider = FakeLLMProvider()  # partner approves by default

    # associate_draft -> partner_loop -> oc_attack -> bolster -> judge_panel(x3)
    assert _run_step(vault, run_id, provider) == [PersonaName.ASSOCIATE.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.PARTNER.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.OC_ASSOCIATE.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.ASSOCIATE.value]  # bolster
    assert _run_step(vault, run_id, provider) == [PersonaName.JUDGE.value] * 3

    assert plan_next(vault, run_id) == []
    assert status_summary(vault, run_id)["status"] == "finished"


def test_partner_loop_respects_cap(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-0002")
    # Partner always demands another revision — the cap must stop the loop.
    revise = {
        "verdict": "revise",
        "critiques": ["narrow it"],
        "instructions": ["add particularity"],
        "self_assessment": "still weak",
    }
    provider = FakeLLMProvider(script={("partner", "partner_loop"): revise})

    personas: list[str] = []
    for _ in range(20):
        if not plan_next(vault, run_id):
            break
        personas.extend(_run_step(vault, run_id, provider))

    # cap associate_partner=2 => at most 2 associate drafts inside the partner loop
    # (initial draft + one redraft), then the loop moves on despite "revise".
    partner_loop_associate = personas.count(PersonaName.ASSOCIATE.value)
    # 2 drafts in the loop + 1 bolster == 3 associate turns total.
    assert partner_loop_associate == 3
    assert personas.count(PersonaName.PARTNER.value) == 2
    assert status_summary(vault, run_id)["status"] == "finished"


def test_derailment_discards_and_respawns_same_turn(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-0003")

    spec = plan_next(vault, run_id)[0]
    first = record_turn(vault, run_id, spec.turn_id, "not valid json", None, NOW)
    assert isinstance(first, DiscardedTurn)
    assert first.attempt == 1

    # The same slot is re-planned with an incremented attempt.
    respawn = plan_next(vault, run_id)
    assert len(respawn) == 1
    assert respawn[0].turn_id == spec.turn_id
    assert respawn[0].attempt == 2


def test_max_attempts_pauses_run(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-0004", max_attempts=3)

    turn_id = plan_next(vault, run_id)[0].turn_id
    for attempt in range(1, 4):
        result = record_turn(
            vault, run_id, turn_id, "garbage", None, NOW, max_attempts=3
        )
        assert isinstance(result, DiscardedTurn)
        assert result.attempt == attempt

    assert status_summary(vault, run_id)["status"] == "needs_attention"
    assert plan_next(vault, run_id) == []  # halted


def test_completed_turn_record_is_idempotent(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-0005")
    provider = FakeLLMProvider()
    spec = plan_next(vault, run_id)[0]
    result = provider.run_turn(spec, render_prompt(spec))
    first = record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
    # Re-recording the same turn returns the stored record, not a new one.
    again = record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
    assert first == again
