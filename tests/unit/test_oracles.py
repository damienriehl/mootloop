"""Hidden synthetic answer-key evaluation and benchmark evidence contracts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mootloop.errors import OracleError
from mootloop.models.benchmarks import (
    BenchmarkArtifactCommitment,
    BenchmarkDimensionVerdict,
    BenchmarkEvidencePack,
    BenchmarkVerdict,
)
from mootloop.models.common import MatterId, RunId
from mootloop.models.run import PersonaName
from mootloop.oracles import OracleCandidate, evaluate_answer_key, load_answer_key

REPO_ROOT = Path(__file__).resolve().parents[2]
ANSWER_KEY = REPO_ROOT / "tests/oracles/answer_keys/northfield-discovery-responses.json"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _candidate(
    request_id: str,
    response_text: str,
    *,
    rfa_disposition: str | None = None,
) -> OracleCandidate:
    return OracleCandidate(
        persona=PersonaName.ASSOCIATE,
        stage="associate_draft",
        request_id=request_id,
        output_schema_name="draft",
        output={
            "response_text": response_text,
            "objections": [],
            "candidate_citations": [],
            "fact_ids_used": [],
            "attorney_gate_items": [],
            "rfa_disposition": rfa_disposition,
            "self_assessment": "Checked only the supplied record.",
        },
    )


def test_hidden_answer_key_accepts_domain_correct_outputs() -> None:
    key = load_answer_key(ANSWER_KEY.read_bytes())
    result = evaluate_answer_key(
        key,
        [
            _candidate("ROG-3", "Granite Supply tendered the assemblies on March 14, 2026."),
            _candidate(
                "RFA-3",
                "Defendant admits it tendered the assemblies on March 14, 2026.",
                rfa_disposition="admit",
            ),
        ],
    )

    assert result.passed
    assert result.failures == ()


def test_evaluator_rejects_seeded_persona_domain_regression() -> None:
    key = load_answer_key(ANSWER_KEY.read_bytes())
    result = evaluate_answer_key(
        key,
        [
            _candidate(
                "ROG-3",
                "The contract price was $148,500 and Plaintiff alleged 120 assemblies.",
            ),
            _candidate(
                "RFA-3",
                "Defendant denies the request for lack of knowledge.",
                rfa_disposition="deny",
            ),
        ],
    )

    assert not result.passed
    assert {failure.case_id for failure in result.failures} == {
        "associate-rog-3-tender-date",
        "associate-rfa-3-tender-admission",
    }
    assert any(failure.code == "missing_required_text" for failure in result.failures)
    assert any(failure.code == "forbidden_text" for failure in result.failures)
    assert any(failure.code == "unexpected_value" for failure in result.failures)


def test_oracle_rejects_missing_duplicate_and_wrong_identity_candidates() -> None:
    key = load_answer_key(ANSWER_KEY.read_bytes())
    wrong = _candidate("ROG-3", "Tender occurred March 14, 2026.")
    wrong = wrong.model_copy(update={"persona": PersonaName.JUDGE})

    missing = evaluate_answer_key(key, [wrong])
    assert not missing.passed
    assert {failure.code for failure in missing.failures} == {
        "candidate_identity_mismatch",
        "missing_candidate",
    }

    with pytest.raises(ValueError, match="duplicate oracle candidate"):
        evaluate_answer_key(key, [wrong, wrong])


def test_answer_key_rejects_unknown_candidate_output_fields() -> None:
    key = load_answer_key(ANSWER_KEY.read_bytes())
    candidate = _candidate("ROG-3", "Tender occurred March 14, 2026.")
    candidate.output["judge_ruling"] = "sustain"

    result = evaluate_answer_key(key, [candidate])

    assert not result.passed
    assert any(failure.code == "invalid_output_schema" for failure in result.failures)


@pytest.mark.parametrize("field", ["text_fields", "expected_values"])
def test_answer_key_rejects_unknown_referenced_schema_fields(field: str) -> None:
    payload = json.loads(ANSWER_KEY.read_text(encoding="utf-8"))
    payload["cases"][0][field] = (
        ["response_typo"] if field == "text_fields" else {"response_typo": None}
    )

    with pytest.raises(OracleError, match="unknown output field"):
        load_answer_key(json.dumps(payload).encode("utf-8"))


def test_benchmark_models_commit_hashes_not_private_content() -> None:
    pack = BenchmarkEvidencePack(
        evidence_pack_id="EP-mootloop-benchmark-run-0001-001",
        source_matter_id=MatterId("synthetic-matter"),
        run_id=RunId("benchmark-run-0001"),
        task="discovery-responses",
        created_at="2026-08-21T00:00:00+00:00",
        context_manifest_sha256=SHA_A,
        rubric_id="discovery-responses-v1",
        rubric_version="1.0",
        candidate=BenchmarkArtifactCommitment(sha256=SHA_A, size_bytes=1200),
        baseline=BenchmarkArtifactCommitment(sha256=SHA_B, size_bytes=1100),
    )
    verdict = BenchmarkVerdict(
        verdict_id="benchmark-verdict-0123456789abcdef",
        evidence_pack_id=pack.evidence_pack_id,
        evidence_pack_sha256=SHA_A,
        source_matter_id=pack.source_matter_id,
        run_id=pack.run_id,
        reviewer="Attorney Example",
        channel="cli",
        recorded_at="2026-08-21T01:00:00+00:00",
        overall="equal",
        dimensions=(
            BenchmarkDimensionVerdict(dimension="legal_correctness", verdict="equal"),
            BenchmarkDimensionVerdict(dimension="grounding", verdict="better"),
        ),
    )

    payload = json.dumps(
        {"evidence": pack.model_dump(mode="json"), "verdict": verdict.model_dump(mode="json")}
    )
    assert "candidate_text" not in payload
    assert "baseline_text" not in payload
    assert "private" not in payload.casefold()
    assert BenchmarkEvidencePack.model_validate_json(pack.model_dump_json()) == pack
    assert BenchmarkVerdict.model_validate_json(verdict.model_dump_json()) == verdict


def test_benchmark_verdict_requires_human_identity_and_complete_dimensions() -> None:
    with pytest.raises(ValueError, match="reviewer"):
        BenchmarkVerdict(
            verdict_id="benchmark-verdict-0123456789abcdef",
            evidence_pack_id="EP-mootloop-benchmark-run-0001-001",
            evidence_pack_sha256=SHA_A,
            source_matter_id=MatterId("synthetic-matter"),
            run_id=RunId("benchmark-run-0001"),
            reviewer="   ",
            channel="api",
            recorded_at="2026-08-21T01:00:00+00:00",
            overall="equal",
            dimensions=(),
        )

    with pytest.raises(ValueError, match="duplicate benchmark dimensions"):
        BenchmarkVerdict(
            verdict_id="benchmark-verdict-0123456789abcdef",
            evidence_pack_id="EP-mootloop-benchmark-run-0001-001",
            evidence_pack_sha256=SHA_A,
            source_matter_id=MatterId("synthetic-matter"),
            run_id=RunId("benchmark-run-0001"),
            reviewer="Attorney Example",
            channel="api",
            recorded_at="2026-08-21T01:00:00+00:00",
            overall="equal",
            dimensions=(
                BenchmarkDimensionVerdict(dimension="grounding", verdict="equal"),
                BenchmarkDimensionVerdict(dimension="grounding", verdict="better"),
            ),
        )


@pytest.mark.parametrize("unsafe_id", ["../other-matter", "UPPERCASE", "a/b"])
def test_benchmark_records_reject_unsafe_matter_and_run_ids(unsafe_id: str) -> None:
    evidence = {
        "schema_version": "1.0",
        "evidence_pack_id": "EP-mootloop-benchmark-run-0001-001",
        "source_matter_id": "synthetic-matter",
        "run_id": "benchmark-run-0001",
        "task": "discovery-responses",
        "created_at": "2026-08-21T00:00:00+00:00",
        "context_manifest_sha256": SHA_A,
        "rubric_id": "discovery-responses-v1",
        "rubric_version": "1.0",
        "candidate": {"sha256": SHA_A, "size_bytes": 1200},
        "baseline": {"sha256": SHA_B, "size_bytes": 1100},
    }
    with pytest.raises(ValueError, match="source_matter_id"):
        BenchmarkEvidencePack.model_validate({**evidence, "source_matter_id": unsafe_id})
    with pytest.raises(ValueError, match="run_id"):
        BenchmarkEvidencePack.model_validate({**evidence, "run_id": unsafe_id})

    verdict = {
        "schema_version": "1.0",
        "verdict_id": "benchmark-verdict-0123456789abcdef",
        "evidence_pack_id": "EP-mootloop-benchmark-run-0001-001",
        "evidence_pack_sha256": SHA_A,
        "source_matter_id": "synthetic-matter",
        "run_id": "benchmark-run-0001",
        "reviewer": "Attorney Example",
        "source": "human",
        "channel": "api",
        "recorded_at": "2026-08-21T01:00:00+00:00",
        "overall": "equal",
        "dimensions": [{"dimension": "grounding", "verdict": "equal"}],
    }
    with pytest.raises(ValueError, match="source_matter_id"):
        BenchmarkVerdict.model_validate({**verdict, "source_matter_id": unsafe_id})
    with pytest.raises(ValueError, match="run_id"):
        BenchmarkVerdict.model_validate({**verdict, "run_id": unsafe_id})
