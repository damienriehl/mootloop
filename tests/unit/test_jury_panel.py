from __future__ import annotations

import pytest
from pydantic import ValidationError

from mootloop.models.panels import JurySignal
from mootloop.models.run import JurorOutput
from mootloop.models.task import PanelConfig
from mootloop.panels import fold_jury_signal
from mootloop.stages import SlotLayout


def _juror(comprehension: int, persuasion: int, concern: str) -> JurorOutput:
    return JurorOutput(
        comprehension_score=comprehension,
        persuasion_score=persuasion,
        confusion_points=[concern],
        credibility_concerns=[],
        directional_only=True,
        self_assessment="A bounded lay readthrough, not a prediction.",
    )


def test_enabled_jury_requires_at_least_one_juror() -> None:
    with pytest.raises(ValidationError, match="jurors"):
        PanelConfig(jury=True, jurors=0)


def test_jury_slots_are_stable_and_follow_restructure() -> None:
    layout = SlotLayout(
        run_id="jury",
        req_index=0,
        ap=2,
        oc=1,
        bolster=1,
        judges=3,
        rubric_panel=3,
        restructure=1,
        jurors=3,
    )

    assert layout.jury_slot(1) == layout.restructure_slot(1) + 1
    assert layout.jury_slot(3) == layout.jury_slot(1) + 2


def test_jury_fold_is_explicitly_directional_and_never_a_gate() -> None:
    signal = fold_jury_signal(
        "run-1",
        "ROG-1",
        [_juror(5, 4, "dense opening"), _juror(3, 2, "unclear chronology")],
    )

    assert isinstance(signal, JurySignal)
    assert signal.directional_only is True
    assert signal.total_readers == 2
    assert signal.mean_comprehension == 4.0
    assert signal.mean_persuasion == 3.0
    assert signal.confusion_samples == ["dense opening", "unclear chronology"]
