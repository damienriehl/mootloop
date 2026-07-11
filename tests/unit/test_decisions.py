"""Attorney-gate decision generation, taxonomy, and resolution (plan P-28/D11)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mootloop.decisions import (
    DecisionStore,
    gate_mode_for,
    open_by_taxonomy,
    resolve,
)
from mootloop.discovery_parser import save_requests
from mootloop.errors import DecisionError
from mootloop.facts import FactStore
from mootloop.journal import load_state
from mootloop.llm import FakeLLMProvider
from mootloop.models.common import DocId
from mootloop.models.decisions import DecisionKind
from mootloop.models.requests import RequestItem, RequestSet, RequestType, make_request_id
from mootloop.orchestrator import run_with_provider, start_run
from mootloop.vault import init_vault, load_matter
from tests.conftest import make_matter

NOW = "2026-07-11T00:00:00+00:00"


def _vault(tmp_path: Path, request_type: RequestType, count: int, *, facts: bool) -> Path:
    vault = tmp_path / "vault"
    init_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    items = [
        RequestItem(
            request_id=make_request_id(request_type, n),
            set_number=1,
            number=n,
            text=f"Request {n} text.",
            source_doc=DocId("doc-servedservedserv"),
        )
        for n in range(1, count + 1)
    ]
    save_requests(
        vault,
        RequestSet(request_type=request_type, set_number=1, title="Set 1", items=items),
    )
    if facts:
        FactStore(vault).add_fact("The contract price was $148,500.", confidence=1.0)
    return vault


def _kinds(vault: Path, run_id: str) -> list[str]:
    return [d.kind.value for d in DecisionStore(vault, run_id).list_all()]


def test_rfa_generates_one_disposition_decision_per_request(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.RFA, 1, facts=True)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-rfa")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)

    rfa = [
        d
        for d in DecisionStore(vault, run_id).list_all()
        if d.kind is DecisionKind.RFA_DISPOSITION
    ]
    assert len(rfa) == 1
    assert rfa[0].proposal.recommended == "deny"  # FakeLLM default RFA disposition
    # RFA disposition is a hard-human gate, so the run halts for the attorney.
    assert load_state(vault, run_id).status == "needs_decisions"


def test_objection_posture_is_one_per_request_type(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY, 3, facts=True)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-obj")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)

    posture = [k for k in _kinds(vault, run_id) if k == "objection_posture"]
    assert len(posture) == 1  # one per type-set across all three ROGs


def test_privilege_objection_generates_privilege_call(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.RFP, 1, facts=True)

    def priv_draft(spec: Any, prompt: str) -> dict[str, Any]:
        return {
            "response_text": "Withholding responsive privileged documents.",
            "objections": [{"basis": "privilege", "text": "Attorney-client privileged."}],
            "candidate_citations": [],
            "fact_ids_used": list(spec.prompt_context.get("fact_ids", []))[:1],
            "attorney_gate_items": [],
            "rfa_disposition": None,
            "self_assessment": "Privilege asserted.",
        }

    provider = FakeLLMProvider(
        script={
            ("associate", "associate_draft"): priv_draft,
            ("associate", "partner_loop"): priv_draft,
            ("associate", "bolster"): priv_draft,
        }
    )
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-priv")
    run_with_provider(vault, run_id, provider, NOW)

    priv = [
        d
        for d in DecisionStore(vault, run_id).list_all()
        if d.kind is DecisionKind.PRIVILEGE_CALL
    ]
    assert len(priv) == 1
    # Privilege is a hard-human gate -> the run cannot finish unresolved.
    assert load_state(vault, run_id).status == "needs_decisions"


def test_unsupported_assertion_from_attorney_gate_items(tmp_path: Path) -> None:
    # No facts -> the FakeLLM default raises an attorney_gate_item every draft.
    vault = _vault(tmp_path, RequestType.INTERROGATORY, 1, facts=False)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-uns")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)

    uns = [
        d
        for d in DecisionStore(vault, run_id).list_all()
        if d.kind is DecisionKind.UNSUPPORTED_ASSERTION
    ]
    assert uns, "expected an unsupported_assertion decision"
    # Only policy-delegable gates -> a ROG run still finishes autonomously.
    assert load_state(vault, run_id).status == "finished"


def test_generation_is_idempotent_across_draft_turns(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY, 1, facts=False)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-idem")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)

    # Several draft/bolster turns ran, but each logical gate appears exactly once.
    decisions = DecisionStore(vault, run_id).list_all()
    keys = [d.dedupe_key for d in decisions]
    assert len(keys) == len(set(keys))


def test_gate_mode_taxonomy(tmp_path: Path) -> None:
    matter = make_matter()
    assert gate_mode_for(matter, DecisionKind.PRIVILEGE_CALL) == "hard-human"
    assert gate_mode_for(matter, DecisionKind.RFA_DISPOSITION) == "hard-human"
    assert gate_mode_for(matter, DecisionKind.OBJECTION_POSTURE) == "policy-delegable"
    assert gate_mode_for(matter, DecisionKind.UNSUPPORTED_ASSERTION) == "policy-delegable"


def test_resolve_happy_path_then_rejects_double_resolution(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY, 1, facts=False)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-res")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)

    decision = DecisionStore(vault, run_id).list_open()[0]
    resolved = resolve(
        vault, run_id, decision.decision_id, "approve", None, "ok", "Atty", "human", NOW
    )
    assert resolved.status == "approved"
    assert resolved.resolution is not None
    assert resolved.resolution.chosen_key == decision.proposal.recommended

    # No status regression: resolving a resolved decision is an error.
    with pytest.raises(DecisionError):
        resolve(vault, run_id, decision.decision_id, "deny", None, "", "Atty", "human", NOW)


def test_resolving_last_hard_human_gate_finishes_the_run(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.RFA, 1, facts=True)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-finish")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    assert load_state(vault, run_id).status == "needs_decisions"

    matter = load_matter(vault)
    for decision in open_by_taxonomy(vault, run_id, matter, "hard-human"):
        resolve(
            vault,
            run_id,
            decision.decision_id,
            "approve",
            decision.proposal.recommended,
            "",
            "Atty",
            "human",
            NOW,
        )
    # The delegable objection_posture is still open, but it does not block finish.
    assert load_state(vault, run_id).status == "finished"


def test_modify_requires_a_chosen_key(tmp_path: Path) -> None:
    vault = _vault(tmp_path, RequestType.INTERROGATORY, 1, facts=False)
    run_id = start_run(vault, "discovery-responses", NOW, run_id="dec-mod")
    run_with_provider(vault, run_id, FakeLLMProvider(), NOW)
    decision = DecisionStore(vault, run_id).list_open()[0]
    with pytest.raises(DecisionError):
        resolve(vault, run_id, decision.decision_id, "modify", None, "", "Atty", "human", NOW)
