"""Shared test fixtures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from mootloop.models.matter import MatterConfig

NOW_ISO = "2026-07-11T00:00:00+00:00"


@pytest.fixture(autouse=True)
def _ephemeral_backup_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test mint/persist a real backup key into ``~/.mootloop/secrets.env``.

    Any code path that reaches `load_or_create_backup_key` (e.g. `mootloop close`'s pre-purge
    backup) gets a per-test in-memory AES key instead. Tests that pass an explicit ``key=``
    bypass this entirely; the secrets loader itself is still exercised directly, with a tmp
    secrets file, in ``test_secrets.py``.
    """
    key = os.urandom(32)
    monkeypatch.setattr(
        "mootloop.engine.backup.load_or_create_backup_key", lambda *a, **k: key
    )


def resolve_all_decisions(
    vault: Path | str, run_id: str, now: str = NOW_ISO, *, by: str = "Test Attorney"
) -> None:
    """Resolve every open attorney-gate decision (approve @ recommendation). Used by
    tests that need a run to advance past the Phase 5 hard-human finish gate."""
    from mootloop.decisions import DecisionStore, resolve

    for decision in DecisionStore(vault, run_id).list_open():
        resolve(
            vault,
            run_id,
            decision.decision_id,
            "approve",
            decision.proposal.recommended,
            "",
            by,
            "human",
            now,
        )


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
