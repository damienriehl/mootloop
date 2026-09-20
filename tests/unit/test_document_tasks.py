from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.context import load_run_context
from mootloop.errors import OrchestratorError
from mootloop.models.document_task import DocumentTaskInput
from mootloop.orchestrator import plan_next, start_run
from mootloop.tasks import get_binding
from mootloop.vault import init_vault
from tests.conftest import make_matter
from tests.unit.test_document_task_context import _input

TASKS = ("complaint", "motion", "appellate-brief", "oral-argument", "business-advice")
NOW = "2026-09-20T00:00:00+00:00"


def document_vault(tmp_path: Path, task: str, strategy: str = "thin-full") -> Path:
    matter = make_matter().model_dump(mode="json")
    matter["pipeline_strategy"] = strategy
    if task == "business-advice":
        matter.update(
            schema_version="1.1",
            matter_kind="advisory",
            client="Business",
            objective="Assess options",
            caption=None,
            our_side=None,
            parties=[],
        )
    from mootloop.models.matter import MatterConfig

    vault = tmp_path / "vault"
    init_vault(vault, MatterConfig.model_validate(matter), registry_path=tmp_path / "canaries.json")
    data = _input().model_dump(mode="json")
    data.update(task=task, court_level="advisory" if task == "business-advice" else "trial")
    (vault / "documents").mkdir(exist_ok=True)
    (vault / "documents" / "motion-a.json").write_text(
        DocumentTaskInput.model_validate(data).model_dump_json()
    )
    return vault


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("strategy", ["thin-full", "deep-core", "adversarial-first"])
def test_document_families_launch_with_narrative_pipeline(
    tmp_path: Path, task: str, strategy: str
) -> None:
    binding = get_binding(task)
    assert binding.config.input_family == "document"
    vault = document_vault(tmp_path, task, strategy)
    run = start_run(vault, task, NOW, run_id="document")
    context = load_run_context(vault, run)
    stages = context.binding.config.stages
    assert "narrative_assessment" in stages
    assert not {"judge_panel", "jury_panel", "restructure"} & set(stages)
    specs = plan_next(vault, run)
    assert [s.request_id for s in specs] == ["motion-a"]
    assert specs[0].output_schema_name == "draft"


def test_idempotent_document_retry_rejects_changed_bytes(tmp_path: Path) -> None:
    vault = document_vault(tmp_path, "motion")
    start_run(vault, "motion", NOW, run_id="document")
    path = vault / "documents" / "motion-a.json"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(OrchestratorError, match="different launch context"):
        start_run(vault, "motion", NOW, run_id="document", idempotent=True)


@pytest.mark.parametrize("task", TASKS)
@pytest.mark.parametrize("strategy", ["thin-full", "deep-core", "adversarial-first"])
def test_document_run_retains_narrative_and_grounding(
    tmp_path: Path, task: str, strategy: str
) -> None:
    from mootloop.decisions import DecisionStore
    from mootloop.gate_ledger import build_ledger
    from mootloop.llm import FakeLLMProvider
    from mootloop.models.run import NarrativeAssessment
    from mootloop.orchestrator import run_with_provider

    vault = document_vault(tmp_path, task, strategy)
    run = start_run(vault, task, NOW, run_id="document")
    provider = FakeLLMProvider()
    state = run_with_provider(vault, run, provider, NOW)
    assert state.status == "finished"
    assert not state.discarded
    stages = {r.spec.stage for r in state.completed_turns.values()}
    assert {
        "associate_draft",
        "oc_attack",
        "bolster",
        "narrative_assessment",
        "rubric_gate",
    } <= stages
    assessments = [
        NarrativeAssessment.model_validate(r.output)
        for r in state.completed_turns.values()
        if r.spec.stage == "narrative_assessment"
    ]
    assert assessments and assessments[0].limitations
    assert not DecisionStore(vault, run).list_all()
    ledger = build_ledger(vault, run)
    assert ledger.gates["motion-a"]["fabrication"] == "pass"
    assert ledger.gates["motion-a"]["completeness"] == "pass"
    assert not ledger.export_ready  # Still requires real human attestation.


def test_document_recovery_keeps_human_blocker_without_discovery_decisions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mootloop import orchestrator
    from mootloop.decisions import DecisionStore
    from mootloop.journal import load_state
    from mootloop.llm import FakeLLMProvider
    from mootloop.models.decisions import DecisionKind
    from mootloop.models.events import RunFinished

    vault = document_vault(tmp_path, "motion")
    import yaml

    matter_path = vault / "matter.yaml"
    matter = yaml.safe_load(matter_path.read_text())
    matter["gates"] = [{"name": "unsupported_assertion", "mode": "hard-human"}]
    matter_path.write_text(yaml.safe_dump(matter))
    run = start_run(vault, "motion", NOW, run_id="document")

    def unsupported(spec, prompt):
        from mootloop.llm import _default_output

        data = _default_output(spec)
        data["attorney_gate_items"] = ["Verify a disputed assumption"]
        return data

    provider = FakeLLMProvider({"associate_draft": unsupported})
    original = orchestrator.append

    def interrupt(root, run_id, event):
        if isinstance(event, RunFinished):
            raise OSError("interrupted finalization")
        original(root, run_id, event)

    monkeypatch.setattr(orchestrator, "append", interrupt)
    with pytest.raises(OSError, match="interrupted finalization"):
        orchestrator.run_with_provider(vault, run, provider, NOW)
    spent = load_state(vault, run).total_spend_usd
    monkeypatch.setattr(orchestrator, "append", original)
    for _ in range(2):
        assert orchestrator.finalize_if_ready(vault, run, NOW).status == "needs_decisions"
    decisions = DecisionStore(vault, run).list_all()
    assert len(decisions) == 1
    assert decisions[0].kind == DecisionKind.UNSUPPORTED_ASSERTION
    assert decisions[0].status == "open"
    assert load_state(vault, run).total_spend_usd == spent


@pytest.mark.parametrize("strategy", ["thin-full", "deep-core", "adversarial-first"])
def test_multiunit_document_turns_are_stable_and_estimate_includes_assessment(
    tmp_path: Path, strategy: str
) -> None:
    import json
    from datetime import date

    from mootloop.llm import FakeLLMProvider
    from mootloop.orchestrator import estimate_run_cost, operative_drafts, run_with_provider

    vault = document_vault(tmp_path, "motion", strategy)
    path = vault / "documents" / "motion-a.json"
    data = json.loads(path.read_text())
    data["units"].append(
        {"unit_id": "motion-b", "title": "Second issue", "instructions": "Address notice"}
    )
    path.write_text(json.dumps(data))
    run = start_run(vault, "motion", NOW, run_id="document")
    first = plan_next(vault, run)
    assert first == plan_next(vault, run)
    assert len({s.turn_id for s in first}) == 2
    provider = FakeLLMProvider()
    state = run_with_provider(vault, run, provider, NOW)
    assert state.status == "finished"
    assert len(provider.calls) == len(set(provider.calls))
    assert all(draft is not None for _, draft in operative_drafts(vault, run))
    estimate = estimate_run_cost(vault, "motion", "moderate", date(2026, 9, 20))
    assessment = [s for s in estimate.breakdown if s.stage == "narrative_assessment"]
    assert len(assessment) == 1
    assert assessment[0].max_calls == 2


def test_document_opponents_only_receive_eligible_public_evidence(tmp_path: Path) -> None:
    import hashlib
    import json

    from mootloop.llm import FakeLLMProvider
    from mootloop.models.run import PersonaName
    from mootloop.orchestrator import run_with_provider

    vault = document_vault(tmp_path, "motion")
    path = vault / "documents" / "motion-a.json"
    data = json.loads(path.read_text())
    private = dict(
        data["evidence"][0], source_id="private", public=False, text="Private-only instruction."
    )
    private["sha256"] = hashlib.sha256(private["text"].encode()).hexdigest()
    data["evidence"].append(private)
    path.write_text(json.dumps(data))
    run = start_run(vault, "motion", NOW, run_id="document")
    state = run_with_provider(vault, run, FakeLLMProvider(), NOW)
    for record in state.completed_turns.values():
        content = str(record.spec.prompt_context["approved_context"])
        assert "A record fact." in content
        if record.spec.persona not in (PersonaName.ASSOCIATE, PersonaName.PARTNER):
            assert "Private-only instruction" not in content


def test_document_report_retains_adverse_assessment_and_prompt_brief(tmp_path: Path) -> None:
    from mootloop.llm import FakeLLMProvider
    from mootloop.orchestrator import run_with_provider
    from mootloop.panels import build_panel_report

    vault = document_vault(tmp_path, "motion")
    run = start_run(vault, "motion", NOW, run_id="document")
    state = run_with_provider(vault, run, FakeLLMProvider(), NOW)
    report = build_panel_report(vault, run)
    assert report.results == []
    assert report.narrative_assessments[0].assessment.disposition == "revise"
    assert report.narrative_assessments[0].draft_turn_id in state.completed_turns
    brief = str(next(iter(state.completed_turns.values())).spec.prompt_context)
    assert "represented_side" in brief and "cutoff" in brief and "preservation_constraints" in brief


def test_document_oversized_evidence_fails_visibly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mootloop import context_assembly

    vault = document_vault(tmp_path, "motion")
    monkeypatch.setattr(context_assembly, "MAX_CONTEXT_ITEM_CHARS", 10)
    with pytest.raises(OrchestratorError, match="context limit"):
        start_run(vault, "motion", NOW, run_id="document")
