"""Oracle candidates pass through the real synthetic plan/prompt/provider path."""

from __future__ import annotations

import shutil
from pathlib import Path

from mootloop.llm import FakeLLMProvider
from mootloop.oracles import candidate_from_raw_turn, evaluate_answer_key, load_answer_key
from mootloop.orchestrator import assemble_prompt, plan_next, start_run

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSWER_KEY = REPO_ROOT / "tests/oracles/answer_keys/northfield-discovery-responses.json"
NOW = "2026-08-21T00:00:00+00:00"


def _regressed_draft(request_id: str) -> dict[str, object]:
    response = (
        "The contract price was $148,500 and Plaintiff alleged 120 assemblies."
        if request_id == "ROG-3"
        else "Defendant denies the request for lack of knowledge."
    )
    return {
        "response_text": response,
        "objections": [],
        "candidate_citations": [],
        "fact_ids_used": [],
        "attorney_gate_items": [],
        "rfa_disposition": "deny" if request_id.startswith("RFA-") else None,
        "self_assessment": "Checked only the supplied record.",
    }


def test_seeded_persona_domain_regression_fails_from_synthetic_run(
    demo_vault: Path, tmp_path: Path
) -> None:
    vault = tmp_path / "vault"
    shutil.copytree(demo_vault, vault)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="oracle-regression-0001")
    specs = {
        str(spec.request_id): spec
        for spec in plan_next(vault, run_id)
        if str(spec.request_id) in {"ROG-3", "RFA-3"}
    }
    assert set(specs) == {"ROG-3", "RFA-3"}

    provider = FakeLLMProvider(
        script={
            spec.turn_id: _regressed_draft(request_id)
            for request_id, spec in specs.items()
        }
    )
    key = load_answer_key(ANSWER_KEY.read_bytes())
    candidates = []
    for spec in specs.values():
        prompt = assemble_prompt(vault, run_id, str(spec.turn_id))
        assert key.isolation_sentinel not in prompt
        result = provider.run_turn(spec, prompt)
        candidates.append(candidate_from_raw_turn(spec, result.text))

    evaluation = evaluate_answer_key(key, candidates)

    assert not evaluation.passed
    assert {failure.case_id for failure in evaluation.failures} == {
        "associate-rog-3-tender-date",
        "associate-rfa-3-tender-admission",
    }
