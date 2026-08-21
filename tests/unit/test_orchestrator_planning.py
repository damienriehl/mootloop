"""plan_next stage-order + loop-cap logic and the derailment/discard contract."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from mootloop import orchestrator, panels
from mootloop.errors import PipelineConfigError
from mootloop.facts import FactStore
from mootloop.gate_ledger import build_ledger
from mootloop.journal import read_events, turn_body_path
from mootloop.llm import FakeLLMProvider, RawTurnResult
from mootloop.models.common import DocId
from mootloop.models.events import GateEvaluated, JournalEvent, TurnCompleted
from mootloop.models.matter import MatterConfig, Panels, Personas
from mootloop.models.requests import RequestItem, RequestSet, RequestType
from mootloop.models.run import DiscardedTurn, PersonaName
from mootloop.orchestrator import (
    assemble_prompt,
    estimate_run_cost,
    plan_next,
    record_turn,
    start_run,
    status_summary,
)
from tests.conftest import make_matter

NOW = "2026-07-11T00:00:00+00:00"


def _build_single_request_vault(
    tmp_path: Path,
    matter: MatterConfig | None = None,
) -> Path:
    from mootloop.vault import init_vault
    vault = tmp_path / "vault"
    init_vault(vault, matter or make_matter(), registry_path=tmp_path / "canaries.json")
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
        result: RawTurnResult = provider.run_turn(
            spec, assemble_prompt(vault, run_id, spec.turn_id)
        )
        record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
        ran.append(spec.persona.value)
    return ran


def test_stage_order_single_request(tmp_path: Path) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-0001")
    provider = FakeLLMProvider()  # partner approves by default

    # associate_draft -> partner critique -> in-loop rubric judge -> (partner approves)
    # -> oc_attack -> bolster -> judge_panel(x3) -> final rubric gate(x3)
    assert _run_step(vault, run_id, provider) == [PersonaName.ASSOCIATE.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.PARTNER.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.RUBRIC_JUDGE.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.OC_ASSOCIATE.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.OC_PARTNER.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.ASSOCIATE.value]  # bolster
    assert _run_step(vault, run_id, provider) == [PersonaName.JUDGE.value] * 3
    assert _run_step(vault, run_id, provider) == [PersonaName.RUBRIC_JUDGE.value] * 3

    assert plan_next(vault, run_id) == []
    assert status_summary(vault, run_id)["status"] == "finished"


def test_optional_jury_is_directional_provenanced_and_never_a_gate(tmp_path: Path) -> None:
    matter = make_matter().model_copy(
        update={"panels": Panels(jury_enabled=True, jurors=2)}
    )
    vault = _build_single_request_vault(tmp_path, matter)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="jury-enabled")
    provider = FakeLLMProvider()

    for _ in range(20):
        specs = plan_next(vault, run_id)
        assert specs
        if specs[0].persona == PersonaName.JUROR:
            break
        _run_step(vault, run_id, provider)
    else:
        pytest.fail("enabled jury stage was never planned")

    assert [spec.persona for spec in specs] == [PersonaName.JUROR, PersonaName.JUROR]
    assert all(spec.stage == "jury_panel" for spec in specs)
    assert all(spec.prompt_context["directional_only"] is True for spec in specs)
    assert all(spec.prompt_context["draft_provenance"]["sha256"] for spec in specs)
    juror_turn_ids = {str(spec.turn_id) for spec in specs}
    assert _run_step(vault, run_id, provider) == [PersonaName.JUROR.value] * 2

    report = panels.build_panel_report(vault, run_id)
    [signal] = report.jury_signals
    assert signal.directional_only is True
    assert signal.total_readers == 2
    juror_gate_events = [
        event
        for event in read_events(vault, run_id)
        if isinstance(event, GateEvaluated) and str(event.turn_id) in juror_turn_ids
    ]
    assert juror_gate_events
    assert {event.result.gate for event in juror_gate_events} == {"degeneracy"}


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


def test_adversarial_first_uses_both_opponents_before_partner_review(tmp_path: Path) -> None:
    matter = make_matter().model_copy(update={"pipeline_strategy": "adversarial-first"})
    vault = _build_single_request_vault(tmp_path, matter)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="adversarial-first")
    provider = FakeLLMProvider()

    assert _run_step(vault, run_id, provider) == [PersonaName.ASSOCIATE.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.OC_ASSOCIATE.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.OC_PARTNER.value]
    assert _run_step(vault, run_id, provider) == [PersonaName.ASSOCIATE.value]
    partner_spec = plan_next(vault, run_id)[0]
    assert partner_spec.persona == PersonaName.PARTNER
    assert partner_spec.prompt_context["draft"]["response_text"] == "Response to ROG-1."


def test_adversarial_first_judges_the_post_partner_revision(tmp_path: Path) -> None:
    matter = make_matter().model_copy(update={"pipeline_strategy": "adversarial-first"})
    vault = _build_single_request_vault(tmp_path, matter)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="adversarial-revision")
    revise = {
        "verdict": "revise",
        "critiques": ["narrow it"],
        "instructions": ["add particularity"],
        "self_assessment": "still weak",
    }
    def partner_review(spec: Any, prompt: str) -> dict[str, Any]:
        del prompt
        if spec.prompt_context["draft"]["response_text"] == "Post-partner corrected response.":
            return {
                "verdict": "approve",
                "critiques": [],
                "instructions": [],
                "self_assessment": "approved",
            }
        return revise

    def revised_draft(spec: Any, prompt: str) -> dict[str, Any]:
        del prompt
        return {
            "response_text": "Post-partner corrected response.",
            "objections": [],
            "candidate_citations": [],
            "fact_ids_used": list(spec.prompt_context["fact_ids"])[:1],
            "attorney_gate_items": [],
            "self_assessment": "corrected",
        }

    provider = FakeLLMProvider(
        script={
            ("partner", "partner_loop"): partner_review,
            ("associate", "partner_loop"): revised_draft,
        }
    )

    for _ in range(12):
        specs = plan_next(vault, run_id)
        assert specs
        if specs[0].persona == PersonaName.JUDGE:
            judge_spec = specs[0]
            break
        _run_step(vault, run_id, provider)
    else:
        pytest.fail("adversarial-first never reached judge review")

    assert judge_spec.persona == PersonaName.JUDGE
    assert judge_spec.prompt_context["draft"]["response_text"] == "Post-partner corrected response."


def test_bypassed_personas_delegate_or_remove_every_owned_stage(tmp_path: Path) -> None:
    personas = Personas(
        associate=False,
        partner=True,
        oc_associate=False,
        oc_partner=True,
        judge=False,
        rubric_judge=False,
    )
    matter = make_matter().model_copy(update={"personas": personas})
    vault = _build_single_request_vault(tmp_path, matter)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="persona-bypass")
    provider = FakeLLMProvider()

    seen: list[str] = []
    for _ in range(10):
        batch = _run_step(vault, run_id, provider)
        if not batch:
            break
        seen.extend(batch)

    assert seen == ["partner", "partner", "oc_partner", "partner"]
    assert status_summary(vault, run_id)["status"] == "finished"
    ledger = build_ledger(vault, run_id)
    assert "rubric" not in ledger.gates["ROG-1"]
    assert "rubric" not in ledger.blockers
    estimate = estimate_run_cost(vault, "discovery-responses", "moderate", date(2026, 8, 21))
    assert [row.stage for row in estimate.breakdown] == [
        "associate_draft",
        "partner_loop:redraft",
        "partner_loop:critique",
        "oc_attack",
        "bolster",
    ]


def test_impossible_pipeline_fails_before_journal_creation(tmp_path: Path) -> None:
    matter = make_matter().model_copy(
        update={"personas": Personas(associate=False, partner=False)}
    )
    vault = _build_single_request_vault(tmp_path, matter)

    with pytest.raises(PipelineConfigError, match="drafting owner"):
        start_run(vault, "discovery-responses", NOW, run_id="no-drafter")

    assert not (vault / "runs" / "no-drafter" / "journal.jsonl").exists()


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
    result = provider.run_turn(spec, assemble_prompt(vault, run_id, spec.turn_id))
    first = record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
    # Re-recording the same turn returns the stored record, not a new one.
    again = record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)
    assert first == again


def test_retry_recovers_sidecar_published_before_turn_completed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-crash-recovery")
    provider = FakeLLMProvider()
    spec = plan_next(vault, run_id)[0]
    result = provider.run_turn(spec, assemble_prompt(vault, run_id, spec.turn_id))
    real_append = orchestrator.append

    def crash_before_completion(
        vault_root: Path | str, target_run_id: str, event: JournalEvent
    ) -> None:
        if isinstance(event, TurnCompleted):
            raise OSError("simulated crash before TurnCompleted")
        real_append(vault_root, target_run_id, event)

    monkeypatch.setattr(orchestrator, "append", crash_before_completion)
    with pytest.raises(OSError, match="simulated crash"):
        record_turn(vault, run_id, spec.turn_id, result.text, result.usage, NOW)

    body = turn_body_path(vault, run_id, spec.turn_id)
    assert body.is_file()
    assert not any(isinstance(event, TurnCompleted) for event in read_events(vault, run_id))

    monkeypatch.setattr(orchestrator, "append", real_append)
    later = "2026-07-12T00:00:00+00:00"
    recovered = record_turn(vault, run_id, spec.turn_id, result.text, result.usage, later)

    assert recovered.completed_at == NOW
    completed = [
        event for event in read_events(vault, run_id) if isinstance(event, TurnCompleted)
    ]
    assert [event.record.completed_at for event in completed] == [NOW]


def test_retry_spec_carries_discard_feedback(tmp_path: Path) -> None:
    """A respawned slot's prompt context names WHY the last attempt was rejected —
    schema errors and gate findings alike — so the redo can self-correct."""
    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="unit-0007")

    spec = plan_next(vault, run_id)[0]
    first = record_turn(vault, run_id, spec.turn_id, '{"unexpected": true}', None, NOW)
    assert isinstance(first, DiscardedTurn)

    respawn = plan_next(vault, run_id)[0]
    assert respawn.turn_id == spec.turn_id
    feedback = respawn.prompt_context["previous_attempt_rejected_because"]
    assert "validation" in feedback
    assert "unexpected" in feedback

    # A first attempt never carries feedback (fresh slots elsewhere are unaffected).
    assert "previous_attempt_rejected_because" not in spec.prompt_context
