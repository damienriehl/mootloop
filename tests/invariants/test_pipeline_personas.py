from __future__ import annotations

from pathlib import Path

from mootloop.models.matter import Personas
from mootloop.models.run import PersonaName
from mootloop.pipeline import ACTIVE_PIPELINE_PERSONAS
from mootloop.resources import PERSONAS_DIR


def test_every_selectable_pipeline_persona_has_exactly_one_body() -> None:
    selectable = {PersonaName(name) for name in Personas.model_fields}

    assert selectable == set(ACTIVE_PIPELINE_PERSONAS)
    assert {
        path.stem.replace("-", "_")
        for path in Path(PERSONAS_DIR).glob("*.md")
        if not path.name.startswith("_")
    } == {persona.value for persona in ACTIVE_PIPELINE_PERSONAS}
