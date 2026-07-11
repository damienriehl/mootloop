"""Locked rubric: the hash lock, correctness scoring math, panel median aggregation,
the final gate, and the deterministic completeness (presence) gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from mootloop.errors import RubricLockError
from mootloop.gates import completeness
from mootloop.models.rubric import Rubric, aggregate_panel, final_gate, load_rubric, sha256_hex
from mootloop.models.run import DraftOutput, Objection
from mootloop.resources import rubric_path

RUBRIC_ID = "discovery-responses-v1.0"


def _rubric() -> Rubric:
    return load_rubric(rubric_path(RUBRIC_ID))


def test_locked_rubric_loads_and_is_locked() -> None:
    rubric = _rubric()
    assert rubric.rubric_id == RUBRIC_ID
    assert rubric.version == "1.0"
    assert rubric.locked is True
    assert {c.id for c in rubric.correctness_criteria("rfa")} >= {"denial-meets-substance"}
    # A denial criterion is RFA-only; it must not leak into rog/rfp scoring.
    assert "denial-meets-substance" not in {c.id for c in rubric.correctness_criteria("rog")}


def test_lock_rejects_in_place_edits(tmp_path: Path) -> None:
    src = rubric_path(RUBRIC_ID).read_text(encoding="utf-8")
    good = tmp_path / "r.yaml"
    good.write_text(src, encoding="utf-8")
    (tmp_path / "r.sha256").write_text(sha256_hex(src) + "\n", encoding="utf-8")
    assert load_rubric(good, lock_path=tmp_path / "r.sha256").locked is True

    good.write_text(src + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(RubricLockError):
        load_rubric(good, lock_path=tmp_path / "r.sha256")


def test_missing_hash_file_for_locked_rubric_is_refused(tmp_path: Path) -> None:
    src = rubric_path(RUBRIC_ID).read_text(encoding="utf-8")
    orphan = tmp_path / "r.yaml"
    orphan.write_text(src, encoding="utf-8")
    with pytest.raises(RubricLockError):
        load_rubric(orphan, lock_path=tmp_path / "missing.sha256")


def test_weighted_score_is_length_normalized() -> None:
    rubric = _rubric()
    # All correctness criteria at 5/5 -> 1.0 regardless of how many apply.
    perfect_rfa = {c.id: 5.0 for c in rubric.correctness_criteria("rfa")}
    perfect_rog = {c.id: 5.0 for c in rubric.correctness_criteria("rog")}
    assert rubric.weighted_score(perfect_rfa, "rfa") == pytest.approx(1.0)
    assert rubric.weighted_score(perfect_rog, "rog") == pytest.approx(1.0)
    # Half marks -> 0.5.
    half = {c.id: 2.5 for c in rubric.correctness_criteria("rog")}
    assert rubric.weighted_score(half, "rog") == pytest.approx(0.5)


def test_panel_uses_median_per_criterion() -> None:
    rubric = _rubric()
    ids = [c.id for c in rubric.correctness_criteria("rog")]
    # Three judges; a lone outlier low score must be dropped by the median.
    panel = [
        dict.fromkeys(ids, 4),
        dict.fromkeys(ids, 4),
        dict.fromkeys(ids, 0),
    ]
    composite, per_crit = aggregate_panel(rubric, panel, "rog")
    assert all(v == 4 for v in per_crit.values())  # median of {4,4,0} == 4
    assert composite == pytest.approx(4 / 5)


def test_final_gate_threshold() -> None:
    rubric = _rubric()
    ids = [c.id for c in rubric.correctness_criteria("rog")]
    high = [dict.fromkeys(ids, 4)] * 3
    low = [dict.fromkeys(ids, 2)] * 3
    assert final_gate(rubric, high, "rog", 0.75).status == "pass"
    assert final_gate(rubric, low, "rog", 0.75).status == "fail"


# --- completeness (presence) gate ------------------------------------------


def _draft(response: str, objections: list[Objection] | None = None) -> DraftOutput:
    return DraftOutput(
        response_text=response,
        objections=objections or [],
        fact_ids_used=["fact-1"],
        self_assessment="ok",
    )


def test_completeness_passes_a_compliant_rog() -> None:
    rubric = _rubric()
    req = "Identify every person with knowledge of the contract."
    draft = _draft(
        "Interrogatory No. 1 is restated. Responding party identifies every person "
        "with knowledge of the contract: Jane Roe and John Doe."
    )
    assert completeness.coverage(draft, rubric, "rog", req) == pytest.approx(1.0)
    assert completeness.evaluate(draft, rubric, "rog", req).status == "pass"


def test_completeness_flags_boilerplate_and_hedge() -> None:
    rubric = _rubric()
    req = "Produce all documents relating to the contract."
    draft = _draft(
        "Subject to and without waiving these objections, responding party will "
        "produce responsive documents; nothing is being withheld.",
        [Objection(basis="scope", text="overly broad and unduly burdensome")],
    )
    result = completeness.evaluate(draft, rubric, "rfp", req)
    assert result.status == "fail"
    codes = {f.code for f in result.findings}
    assert "no-boilerplate-general-objection" in codes
    assert "no-hedge-subject-to" in codes


def test_completeness_requires_rfa_reasonable_inquiry() -> None:
    rubric = _rubric()
    req = "Admit the contract was breached."
    missing = _draft("Responding party lacks knowledge sufficient to admit or deny.")
    assert completeness.evaluate(missing, rubric, "rfa", req).status == "fail"
    complete = _draft(
        "After reasonable inquiry, the information known or readily obtainable is "
        "insufficient to admit or deny, so responding party lacks knowledge."
    )
    assert completeness.evaluate(complete, rubric, "rfa", req).status == "pass"
