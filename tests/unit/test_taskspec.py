"""Begin-task on-ramp (plan FE-2.5): deterministic freeform resolution, the append-only
TaskSpec store, and the RunStarted -> TaskSpec breadcrumb round-trip.

The freeform lane is DETERMINISTIC in v1 — a registry-key or keyword substring match,
no LLM — and an unmapped intent is still recorded (``task=None``, not runnable) so every
begin-task attempt leaves an audit trail.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mootloop import taskspec as taskspec_svc
from mootloop.models.taskspec import TaskSpec
from mootloop.taskspec import (
    TaskSpecStore,
    create_freeform,
    list_specs,
    make_task_spec_id,
    resolve_freeform,
)

NOW = "2026-07-11T00:00:00+00:00"
MATTER = "acme-v-widgets"


# --- freeform resolution (deterministic keyword / registry map) --------------


@pytest.mark.parametrize(
    "intent",
    [
        "help me answer the discovery served on us",
        "respond to their INTERROGATORIES",
        "the interrogatory responses are due",
        "draft objections to their interrogator questions",
        "answer the RFP",
        "we need to handle the rfa",
        "prepare a response to their request for production of documents",
        "respond to each request for admission",
    ],
)
def test_keywords_map_to_discovery_responses(intent: str) -> None:
    assert resolve_freeform(intent) == "discovery-responses"


def test_direct_registry_key_match_resolves() -> None:
    # An exact registered-task-key mention wins directly.
    assert resolve_freeform("please start discovery-responses now") == "discovery-responses"


def test_resolution_is_casefolded() -> None:
    assert resolve_freeform("HANDLE THE DISCOVERY") == "discovery-responses"


def test_unmapped_intent_resolves_to_none() -> None:
    assert resolve_freeform("draft an appellate brief about nothing") is None


# --- append-only JSONL store round-trip --------------------------------------


def test_create_freeform_persists_and_round_trips(tmp_path: Path) -> None:
    spec = create_freeform(tmp_path, MATTER, "answer the discovery", NOW)
    assert spec.task == "discovery-responses"
    assert spec.runnable is True
    assert spec.source_lane == "freeform"
    assert spec.matter_id == MATTER
    assert spec.intent_text == "answer the discovery"

    # It landed on disk at the append-only path and reads back byte-identical.
    stored = list_specs(tmp_path)
    assert len(stored) == 1
    assert stored[0] == spec
    assert stored[0].model_dump_json() == spec.model_dump_json()


def test_unmapped_spec_is_recorded_but_not_runnable(tmp_path: Path) -> None:
    spec = create_freeform(tmp_path, MATTER, "draft an appellate brief about nothing", NOW)
    assert spec.task is None
    assert spec.runnable is False
    # Still recorded for the audit trail (the "cannot start a run yet" state).
    assert list_specs(tmp_path) == [spec]


def test_multiple_specs_append_in_order(tmp_path: Path) -> None:
    a = create_freeform(tmp_path, MATTER, "answer the discovery", NOW)
    b = create_freeform(tmp_path, MATTER, "draft a nonexistent thing", NOW)
    c = create_freeform(tmp_path, MATTER, "respond to their RFAs", NOW)
    specs = list_specs(tmp_path)
    assert [s.task_spec_id for s in specs] == [a.task_spec_id, b.task_spec_id, c.task_spec_id]
    # The store never rewrites earlier lines: the file has exactly three JSONL rows.
    raw = (tmp_path / "tasks" / "specs.jsonl").read_text(encoding="utf-8")
    assert len([ln for ln in raw.splitlines() if ln.strip()]) == 3


def test_spec_ids_are_unique(tmp_path: Path) -> None:
    ids = {
        create_freeform(tmp_path, MATTER, "answer the discovery", NOW).task_spec_id
        for _ in range(50)
    }
    assert len(ids) == 50
    # And the id generator itself is collision-resistant at a single timestamp.
    assert len({make_task_spec_id(NOW) for _ in range(50)}) == 50


def test_list_specs_empty_when_no_store(tmp_path: Path) -> None:
    assert list_specs(tmp_path) == []


def test_store_get_by_id(tmp_path: Path) -> None:
    spec = create_freeform(tmp_path, MATTER, "answer the discovery", NOW)
    store = TaskSpecStore(tmp_path)
    assert store.get(spec.task_spec_id) == spec
    assert store.get("taskspec-does-not-exist") is None


# --- VersionedModel extra="forbid" -------------------------------------------


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    spec = create_freeform(tmp_path, MATTER, "answer the discovery", NOW)
    payload = spec.model_dump()
    payload["surprise"] = "unexpected"
    with pytest.raises(ValidationError) as exc:
        TaskSpec.model_validate(payload)
    # The unknown field is named in the error (extra="forbid" on VersionedModel).
    assert "surprise" in str(exc.value)


# --- RunStarted -> TaskSpec breadcrumb round-trip ----------------------------


def _build_single_request_vault(tmp_path: Path) -> Path:
    from mootloop.discovery_parser import save_requests
    from mootloop.facts import FactStore
    from mootloop.models.common import DocId
    from mootloop.models.requests import RequestItem, RequestSet, RequestType
    from mootloop.vault import init_vault
    from tests.conftest import make_matter

    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    save_requests(
        vault,
        RequestSet(
            request_type=RequestType.INTERROGATORY,
            set_number=1,
            title="Interrogatories Set 1",
            items=[
                RequestItem(
                    request_id="ROG-1",  # type: ignore[arg-type]
                    set_number=1,
                    number=1,
                    text="Identify every person with knowledge of the contract.",
                    source_doc=DocId("doc-servedservedserv"),
                )
            ],
        ),
    )
    FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    return vault


def _run_started(vault: Path, run_id: str) -> object:
    from mootloop.journal import read_events
    from mootloop.models.events import RunStarted

    started = [e for e in read_events(vault, run_id) if isinstance(e, RunStarted)]
    assert len(started) == 1
    return started[0]


def test_start_run_records_task_spec_id_on_run_started(tmp_path: Path) -> None:
    from mootloop.orchestrator import start_run

    vault = _build_single_request_vault(tmp_path)
    spec = create_freeform(vault, MATTER, "answer the discovery", NOW)
    run_id = start_run(vault, "discovery-responses", NOW, task_spec_id=spec.task_spec_id)
    event = _run_started(vault, run_id)
    assert event.task_spec_id == spec.task_spec_id  # type: ignore[attr-defined]


def test_start_run_task_spec_id_defaults_none(tmp_path: Path) -> None:
    from mootloop.orchestrator import start_run

    vault = _build_single_request_vault(tmp_path)
    run_id = start_run(vault, "discovery-responses", NOW)
    assert _run_started(vault, run_id).task_spec_id is None  # type: ignore[attr-defined]


def test_service_module_reexports() -> None:
    # The service surface the routes import from stays stable.
    assert taskspec_svc.create_freeform is create_freeform
    assert taskspec_svc.list_specs is list_specs
    assert taskspec_svc.resolve_freeform is resolve_freeform
