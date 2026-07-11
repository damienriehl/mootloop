"""Unit tests for domain models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mootloop.models.matter import MatterConfig


def _minimal_dict() -> dict:
    return {
        "schema_version": "1.0",
        "matter_id": "acme-v-widgets",
        "caption": {
            "court_name": "District Court, Hennepin County",
            "case_number": "27-CV-26-1234",
            "county": "Hennepin",
        },
        "jurisdiction": {"state": "MN", "forum": "state"},
        "parties": [
            {"name": "Acme Corp", "role": "plaintiff"},
            {"name": "Widgets Inc", "role": "defendant"},
        ],
        "our_side": "defendant",
        "retention": {"retention_class": "standard"},
    }


def test_minimal_config_applies_defaults() -> None:
    cfg = MatterConfig.model_validate(_minimal_dict())
    # All six personas default enabled.
    assert cfg.personas.associate and cfg.personas.cite_checker
    # Jury off by default.
    assert cfg.panels.jury_enabled is False
    # Default gate table present with correct modes.
    modes = {g.name: g.mode for g in cfg.gates}
    assert modes["privilege"] == "hard-human"
    assert modes["rfa_disposition"] == "hard-human"
    assert modes["attestation"] == "hard-human"
    assert modes["objection_posture"] == "policy-delegable"
    assert modes["unsupported_assertion"] == "policy-delegable"
    assert cfg.budget.tier == "moderate"
    assert cfg.retention.litigation_hold is False
    assert cfg.deadlines == []


def test_extra_field_forbidden_names_field() -> None:
    data = _minimal_dict()
    data["mispelled_extra"] = True
    with pytest.raises(ValidationError) as exc:
        MatterConfig.model_validate(data)
    assert "mispelled_extra" in str(exc.value)


def test_missing_required_field_names_it() -> None:
    data = _minimal_dict()
    del data["our_side"]
    with pytest.raises(ValidationError) as exc:
        MatterConfig.model_validate(data)
    assert "our_side" in str(exc.value)


def test_invalid_matter_id_rejected() -> None:
    data = _minimal_dict()
    data["matter_id"] = "Bad ID!"
    with pytest.raises(ValidationError):
        MatterConfig.model_validate(data)


def test_invalid_forum_literal_rejected() -> None:
    data = _minimal_dict()
    data["jurisdiction"]["forum"] = "municipal"
    with pytest.raises(ValidationError) as exc:
        MatterConfig.model_validate(data)
    assert "forum" in str(exc.value)


def test_bad_party_role_rejected() -> None:
    data = _minimal_dict()
    data["parties"][0]["role"] = "witness"
    with pytest.raises(ValidationError):
        MatterConfig.model_validate(data)
