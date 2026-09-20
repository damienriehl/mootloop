"""Offline projection of the original synthetic demo; never shipped as runtime code."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import HTTPException

from mootloop.context import load_run_context
from mootloop.decisions import DecisionStore
from mootloop.export import deliverables_dir
from mootloop.gate_ledger import build_ledger
from mootloop.journal import load_state
from mootloop.models.matter import MatterConfig
from mootloop.models.panels import PanelReport
from mootloop.models.requests import code_from_request_id
from mootloop.models.run import DraftOutput
from mootloop.orchestrator import operative_drafts, status_summary
from mootloop.vault import load_matter

_SERVABLE_SUFFIXES = {".md": "text/markdown", ".json": "application/json"}


def _run_id(vault: Path) -> str:
    runs = vault / "runs"
    if runs.is_dir():
        for entry in sorted(runs.iterdir()):
            if entry.is_dir():
                return entry.name
    raise HTTPException(status_code=503, detail="demo vault has no baked run")


def _matter_summary(matter: MatterConfig) -> dict[str, Any]:
    """The sanitized public view of the (synthetic) matter."""
    attorney = (
        {"name": matter.attorney.name, "firm": matter.attorney.firm} if matter.attorney else None
    )
    return {
        "matter_id": matter.matter_id,
        "caption": {
            "court_name": matter.caption.court_name,
            "case_number": matter.caption.case_number,
            "county": matter.caption.county,
            "judge_name": matter.caption.judge_name,
        }
        if matter.caption
        else None,
        "jurisdiction": {"state": matter.jurisdiction.state, "forum": matter.jurisdiction.forum},
        "parties": [{"name": p.name, "role": p.role} for p in matter.parties],
        "our_side": matter.our_side,
        "attorney": attorney,
        "synthetic": True,
    }


def api_matter(vault: Path) -> dict[str, Any]:
    return _matter_summary(load_matter(vault))


def api_run(vault: Path) -> dict[str, Any]:
    run_id = _run_id(vault)
    summary = status_summary(vault, run_id)
    state = load_state(vault, run_id)
    run_context = load_run_context(vault, run_id)
    config = run_context.binding.config
    persona_turns: dict[str, int] = {}
    for record in state.completed_turns.values():
        persona = record.spec.persona.value
        persona_turns[persona] = persona_turns.get(persona, 0) + 1
    ledger = build_ledger(vault, run_id)
    summary.update(
        {
            "stages": list(config.stages),
            "persona_turns": persona_turns,
            "rubric_version": state.rubric_version,
            "export_ready": ledger.export_ready,
            "blockers": ledger.blockers,
        }
    )
    return summary


def api_requests(vault: Path) -> list[dict[str, Any]]:
    run_id = _run_id(vault)
    state = load_state(vault, run_id)
    ledger = build_ledger(vault, run_id)
    drafts = {str(item.request_id): draft for item, draft in operative_drafts(vault, run_id)}
    turn_counts: dict[str, int] = {}
    restructured: set[str] = set()
    for record in state.completed_turns.values():
        rid = str(record.spec.request_id) if record.spec.request_id else None
        if rid is None:
            continue
        turn_counts[rid] = turn_counts.get(rid, 0) + 1
        if record.spec.stage == "restructure":
            restructured.add(rid)
    family_order = {"rog": 0, "rfp": 1, "rfa": 2}
    units = sorted(
        load_run_context(vault, run_id).units,
        key=lambda u: (
            family_order.get(code_from_request_id(str(u.request_id)), 9),
            u.set_number,
            u.number,
        ),
    )
    out: list[dict[str, Any]] = []
    for unit in units:
        rid = str(unit.request_id)
        draft = drafts.get(rid)
        out.append(
            {
                "request_id": rid,
                "set_number": unit.set_number,
                "number": unit.number,
                "text": unit.text,
                "gates": ledger.gates.get(rid, {}),
                "turns": turn_counts.get(rid, 0),
                "restructured": rid in restructured,
                "objections": len(draft.objections) if draft else 0,
                "rfa_disposition": draft.rfa_disposition if draft else None,
            }
        )
    return out


def _require_request(vault: Path, run_id: str, request_id: str) -> str:
    known = {str(u.request_id) for u in load_run_context(vault, run_id).units}
    if request_id not in known:
        raise HTTPException(status_code=404, detail=f"unknown request {request_id!r}")
    return request_id


def api_request_turns(vault: Path, request_id: str) -> list[dict[str, Any]]:
    run_id = _run_id(vault)
    _require_request(vault, run_id, request_id)
    state = load_state(vault, run_id)
    records = sorted(
        (r for r in state.completed_turns.values() if str(r.spec.request_id) == request_id),
        key=lambda r: r.spec.turn_id,
    )
    return [
        {
            "turn_id": r.spec.turn_id,
            "persona": r.spec.persona.value,
            "stage": r.spec.stage,
            "attempt": r.spec.attempt,
            "model": r.spec.model,
            "completed_at": r.completed_at,
            "output": r.output,
            "gates": [{"gate": g.gate, "status": g.status} for g in r.gate_results],
        }
        for r in records
    ]


def api_request_panel(vault: Path, request_id: str) -> list[dict[str, Any]]:
    run_id = _run_id(vault)
    _require_request(vault, run_id, request_id)
    report_path = vault / "runs" / run_id / "scores" / "panels" / "report.json"
    if not report_path.is_file():
        return []
    report = PanelReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    return [
        {
            "objection_index": r.objection_index,
            "objection_basis": r.objection_basis,
            "survive_votes": r.survive_votes,
            "total_votes": r.total_votes,
            "survival_rate": r.survival_rate,
            "reasoning_samples": r.reasoning_samples,
        }
        for r in report.for_request(request_id)
    ]


def api_request_response(vault: Path, request_id: str) -> dict[str, Any]:
    run_id = _run_id(vault)
    _require_request(vault, run_id, request_id)
    draft: DraftOutput | None = None
    for item, item_draft in operative_drafts(vault, run_id):
        if str(item.request_id) == request_id:
            draft = item_draft
            break
    if draft is None:
        raise HTTPException(status_code=404, detail="no operative draft for this request")
    return draft.model_dump(mode="json")


def api_decisions(vault: Path) -> list[dict[str, Any]]:
    run_id = _run_id(vault)
    load_run_context(vault, run_id)
    decisions = DecisionStore(vault, run_id).list_all()
    decisions.sort(key=lambda d: d.decision_id)
    return [d.model_dump(mode="json") for d in decisions]


def api_gates(vault: Path) -> dict[str, Any]:
    return build_ledger(vault, _run_id(vault)).to_dict()


def _deliverable_files(vault: Path, run_id: str) -> dict[str, Path]:
    """Servable deliverables keyed by their relative name (posix, sorted)."""
    base = deliverables_dir(vault, run_id)
    out: dict[str, Path] = {}
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if path.is_file() and path.suffix in _SERVABLE_SUFFIXES:
                out[path.relative_to(base).as_posix()] = path
    return out


def api_deliverables(vault: Path) -> list[dict[str, str]]:
    files = _deliverable_files(vault, _run_id(vault))
    return [
        {"name": name, "media_type": _SERVABLE_SUFFIXES[path.suffix]}
        for name, path in files.items()
    ]


def api_sets(vault: Path) -> list[dict[str, Any]]:
    request_sets = load_run_context(vault, _run_id(vault)).manifest.request_sets
    return [
        {
            "request_type": s.request_type.value,
            "set_number": s.set_number,
            "title": s.title,
            "requests": len([i for i in s.items if i.subpart is None]),
        }
        for s in request_sets
    ]


def project_legacy(vault: Path, destination: Path) -> str:
    """Write only enumerated public responses from the original fictional demo."""
    import hashlib

    from fastapi.encoders import jsonable_encoder

    from mootloop.models.demo import LegacyProjection
    from mootloop.vault import enclosing_git_repo
    from mootloop.web.catalog import PublicationError

    if enclosing_git_repo(destination) is not None:
        raise PublicationError("legacy projections must remain outside Git checkouts")
    if load_matter(vault).matter_id != "northfield-widgets-v-granite-supply":
        raise PublicationError("legacy projection accepts only the original fictional demo")
    responses = {
        "matter": api_matter(vault),
        "run": api_run(vault),
        "requests": api_requests(vault),
        "decisions": api_decisions(vault),
        "gates": api_gates(vault),
        "deliverables": api_deliverables(vault),
        "sets": api_sets(vault),
    }
    for row in api_requests(vault):
        rid = row["request_id"]
        for view, reader in (
            ("turns", api_request_turns),
            ("panel", api_request_panel),
            ("response", api_request_response),
        ):
            responses[f"requests/{rid}/{view}"] = reader(vault, rid)
    deliverables = {}
    for name, path in _deliverable_files(vault, _run_id(vault)).items():
        base = deliverables_dir(vault, _run_id(vault)).resolve()
        if path.is_symlink() or not path.resolve().is_relative_to(base):
            raise PublicationError("legacy deliverable escapes its source directory")
        deliverables[name] = path.read_text(encoding="utf-8")
    model = LegacyProjection.model_validate(
        {"responses": jsonable_encoder(responses), "deliverables": deliverables}
    )
    raw = model.model_dump_json(indent=2).encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()
