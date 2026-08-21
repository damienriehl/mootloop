from __future__ import annotations

import re
from pathlib import Path

from mootloop.models.matter import Personas
from mootloop.models.pipeline import AUTHORED_AUXILIARY_PERSONAS
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
    } == {
        persona.value
        for persona in (*ACTIVE_PIPELINE_PERSONAS, *AUTHORED_AUXILIARY_PERSONAS)
    }


def test_persona_bodies_do_not_embed_discovery_task_prose() -> None:
    task_prose = re.compile(
        r"served request|motion to compel|rule 36|\brfa\b|interrogator|request for production"
    )

    for path in Path(PERSONAS_DIR).glob("*.md"):
        if path.name.startswith("_"):
            continue
        body = path.read_text(encoding="utf-8").lower()
        assert task_prose.search(body) is None, path
