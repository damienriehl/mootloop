"""Shared test fixtures."""

from __future__ import annotations

import pytest

from mootloop.models.matter import MatterConfig


def make_matter(matter_id: str = "acme-v-widgets") -> MatterConfig:
    return MatterConfig.model_validate(
        {
            "schema_version": "1.0",
            "matter_id": matter_id,
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
    )


@pytest.fixture
def matter() -> MatterConfig:
    return make_matter()
