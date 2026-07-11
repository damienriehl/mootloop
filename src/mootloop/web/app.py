"""Read-only demo API over a pre-baked synthetic vault (`MOOTLOOP_DEMO_VAULT`).

Every response is derived from vault files via the existing fold/reader functions —
no new state, no writes. This module imports ZERO write-capable functions and never
imports `mootloop.web.bake` (the demo tier's only writer); an invariant test
enforces both. No secrets, no uploads, no LLM calls: the vault was baked at image
build time from the synthetic fixture matter.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

import mootloop
from mootloop.decisions import DecisionStore
from mootloop.export import deliverables_dir, load_request_sets
from mootloop.gate_ledger import build_ledger
from mootloop.journal import load_state
from mootloop.models.matter import MatterConfig
from mootloop.models.panels import PanelReport
from mootloop.models.requests import code_from_request_id
from mootloop.models.run import DraftOutput
from mootloop.orchestrator import load_request_units, operative_drafts, status_summary
from mootloop.tasks import get_binding
from mootloop.vault import load_matter

DEFAULT_VAULT = "/app/demo-vault"
VAULT_ENV = "MOOTLOOP_DEMO_VAULT"

_STATIC_DIR = Path(__file__).parent / "static"

# Deliverable content types the demo serves; anything else fails closed (404).
_SERVABLE_SUFFIXES = {".md": "text/markdown", ".json": "application/json"}

app = FastAPI(
    title="MootLoop demo",
    description="Read-only demo of the agentic-law-firm arc on a synthetic matter.",
    version=mootloop.__version__,
)


# --- vault resolution (read-only) --------------------------------------------


def _vault_root() -> Path:
    root = Path(os.environ.get(VAULT_ENV, DEFAULT_VAULT))
    if not (root / "matter.yaml").is_file():
        raise HTTPException(status_code=503, detail="demo vault not available")
    return root


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
        {"name": matter.attorney.name, "firm": matter.attorney.firm}
        if matter.attorney
        else None
    )
    return {
        "matter_id": matter.matter_id,
        "caption": {
            "court_name": matter.caption.court_name,
            "case_number": matter.caption.case_number,
            "county": matter.caption.county,
            "judge_name": matter.caption.judge_name,
        },
        "jurisdiction": {
            "state": matter.jurisdiction.state,
            "forum": matter.jurisdiction.forum,
        },
        "parties": [{"name": p.name, "role": p.role} for p in matter.parties],
        "our_side": matter.our_side,
        "attorney": attorney,
        "synthetic": True,
    }


# --- endpoints ----------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": mootloop.__version__}


@app.get("/api/matter")
def api_matter() -> dict[str, Any]:
    return _matter_summary(load_matter(_vault_root()))


@app.get("/api/run")
def api_run() -> dict[str, Any]:
    vault = _vault_root()
    run_id = _run_id(vault)
    summary = status_summary(vault, run_id)
    state = load_state(vault, run_id)
    if state.task is None:
        raise HTTPException(status_code=503, detail="baked run has no RunStarted event")
    config = get_binding(state.task).config
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


@app.get("/api/requests")
def api_requests() -> list[dict[str, Any]]:
    vault = _vault_root()
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
        load_request_units(vault),
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


def _require_request(vault: Path, request_id: str) -> str:
    known = {str(u.request_id) for u in load_request_units(vault)}
    if request_id not in known:
        raise HTTPException(status_code=404, detail=f"unknown request {request_id!r}")
    return request_id


@app.get("/api/requests/{request_id}/turns")
def api_request_turns(request_id: str) -> list[dict[str, Any]]:
    vault = _vault_root()
    run_id = _run_id(vault)
    _require_request(vault, request_id)
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
            "gates": [
                {"gate": g.gate, "status": g.status} for g in r.gate_results
            ],
        }
        for r in records
    ]


@app.get("/api/requests/{request_id}/panel")
def api_request_panel(request_id: str) -> list[dict[str, Any]]:
    vault = _vault_root()
    run_id = _run_id(vault)
    _require_request(vault, request_id)
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


@app.get("/api/requests/{request_id}/response")
def api_request_response(request_id: str) -> dict[str, Any]:
    vault = _vault_root()
    run_id = _run_id(vault)
    _require_request(vault, request_id)
    draft: DraftOutput | None = None
    for item, item_draft in operative_drafts(vault, run_id):
        if str(item.request_id) == request_id:
            draft = item_draft
            break
    if draft is None:
        raise HTTPException(status_code=404, detail="no operative draft for this request")
    return draft.model_dump(mode="json")


@app.get("/api/decisions")
def api_decisions() -> list[dict[str, Any]]:
    vault = _vault_root()
    run_id = _run_id(vault)
    decisions = DecisionStore(vault, run_id).list_all()
    decisions.sort(key=lambda d: d.decision_id)
    return [d.model_dump(mode="json") for d in decisions]


@app.get("/api/gates")
def api_gates() -> dict[str, Any]:
    vault = _vault_root()
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


@app.get("/api/deliverables")
def api_deliverables() -> list[dict[str, str]]:
    vault = _vault_root()
    files = _deliverable_files(vault, _run_id(vault))
    return [
        {"name": name, "media_type": _SERVABLE_SUFFIXES[path.suffix]}
        for name, path in files.items()
    ]


@app.get("/api/deliverables/{name:path}")
def api_deliverable(name: str) -> PlainTextResponse:
    vault = _vault_root()
    run_id = _run_id(vault)
    # Fail closed on anything path-shaped before touching the filesystem.
    parts = name.split("/")
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise HTTPException(status_code=400, detail="invalid deliverable name")
    base = deliverables_dir(vault, run_id).resolve()
    candidate = (base / name).resolve()
    if base not in candidate.parents:
        raise HTTPException(status_code=400, detail="invalid deliverable name")
    media_type = _SERVABLE_SUFFIXES.get(candidate.suffix)
    if media_type is None or not candidate.is_file():
        raise HTTPException(status_code=404, detail=f"unknown deliverable {name!r}")
    return PlainTextResponse(
        candidate.read_text(encoding="utf-8"), media_type=media_type
    )


@app.get("/api/sets")
def api_sets() -> list[dict[str, Any]]:
    vault = _vault_root()
    return [
        {
            "request_type": s.request_type.value,
            "set_number": s.set_number,
            "title": s.title,
            "requests": len([i for i in s.items if i.subpart is None]),
        }
        for s in load_request_sets(vault)
    ]


# --- viewer (static, built in the viewer unit) --------------------------------


@app.get("/", include_in_schema=False, response_model=None)
def index() -> FileResponse | JSONResponse:
    index_path = _STATIC_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path, media_type="text/html")
    return JSONResponse({"service": "mootloop-demo", "docs": "/docs", "health": "/health"})


if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
