"""FakeLLMProvider resolution + RecordingProvider + cost metering."""

from __future__ import annotations

import json
from pathlib import Path

from mootloop.llm import (
    FakeLLMProvider,
    RecordingProvider,
)
from mootloop.models.run import (
    SCHEMA_CRITIQUE,
    SCHEMA_DRAFT,
    SCHEMA_JUDGE,
    DraftOutput,
    JudgeOutput,
    PersonaName,
    TurnSpec,
)


def _spec(schema: str, stage: str, persona: PersonaName, **ctx: object) -> TurnSpec:
    return TurnSpec(
        turn_id="run-1-t0000",
        run_id="run-1",
        persona=persona,
        stage=stage,
        prompt_context=dict(ctx),
        output_schema_name=schema,
    )


def test_default_draft_is_schema_valid_and_clean() -> None:
    provider = FakeLLMProvider()
    spec = _spec(SCHEMA_DRAFT, "associate_draft", PersonaName.ASSOCIATE, fact_ids=["fact-1"])
    result = provider.run_turn(spec, "prompt text")
    draft = DraftOutput.model_validate_json(result.text)
    assert draft.fact_ids_used == ["fact-1"]
    assert provider.calls == ["run-1-t0000"]


def test_default_judge_output() -> None:
    provider = FakeLLMProvider()
    spec = _spec(SCHEMA_JUDGE, "judge_panel", PersonaName.JUDGE)
    out = JudgeOutput.model_validate_json(provider.run_turn(spec, "p").text)
    assert len(out.rulings) == 1


def test_script_resolution_prefers_turn_id() -> None:
    canned = {
        "verdict": "revise",
        "critiques": ["too broad"],
        "instructions": ["narrow it"],
        "self_assessment": "needs work",
    }
    provider = FakeLLMProvider(script={"run-1-t0000": canned})
    spec = _spec(SCHEMA_CRITIQUE, "partner_loop", PersonaName.PARTNER)
    assert json.loads(provider.run_turn(spec, "p").text)["verdict"] == "revise"


def test_script_callable_by_stage() -> None:
    def make(spec: TurnSpec, prompt: str) -> dict[str, object]:
        return {
            "verdict": "approve",
            "critiques": [],
            "instructions": [],
            "self_assessment": f"reviewed {spec.request_id}",
        }

    provider = FakeLLMProvider(script={"partner_loop": make})
    spec = _spec(SCHEMA_CRITIQUE, "partner_loop", PersonaName.PARTNER)
    assert "reviewed" in json.loads(provider.run_turn(spec, "p").text)["self_assessment"]


def test_recording_provider_persists_prompt(tmp_path: Path) -> None:
    inner = FakeLLMProvider()
    provider = RecordingProvider(inner, tmp_path)
    spec = _spec(SCHEMA_DRAFT, "associate_draft", PersonaName.ASSOCIATE)
    provider.run_turn(spec, "the rendered prompt")
    saved = (tmp_path / "run-1-t0000.prompt.txt").read_text(encoding="utf-8")
    assert saved == "the rendered prompt"
    assert inner.calls == ["run-1-t0000"]


def test_fake_provider_honors_tier_model() -> None:
    # The tier-resolved model rides on the spec; usage echoes it for metering.
    provider = FakeLLMProvider()
    spec = _spec(SCHEMA_DRAFT, "associate_draft", PersonaName.ASSOCIATE)
    assert provider.run_turn(spec, "p").usage.model == "fake"
    spec_priced = spec.model_copy(update={"model": "claude-opus-4-8"})
    assert provider.run_turn(spec_priced, "p").usage.model == "claude-opus-4-8"
