"""Write-tier matter API routes — thin adapters over the existing tested services.

Every matter-data route resolves its matter through the registry (charset +
realpath-containment) and records a hash-chained access-audit entry; mutating routes
additionally require a valid Access JWT and a matching CSRF token. Service errors
(lock contention, unknown decision, blocked attestation, containment breach) surface
as typed HTTP responses via the app's exception handlers — the route bodies stay thin.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import mootloop
from mootloop import attest as attest_svc
from mootloop import decisions as decisions_svc
from mootloop import orchestrator
from mootloop import taskspec as taskspec_svc
from mootloop.engine.queue import Queue as WorkQueue
from mootloop.engine.queue import WorkItem
from mootloop.errors import OrchestratorError
from mootloop.export import link as link_svc
from mootloop.journal import load_state
from mootloop.models.matters import MatterSummary
from mootloop.registry import MatterRegistry
from mootloop.vault import safe_vault_path
from mootloop.web import audit
from mootloop.web.api import deps, models, readers
from mootloop.web.api.deps import (
    get_link_signer,
    get_queue,
    get_registry,
    issue_csrf_token,
    require_access,
    require_csrf,
    require_internal,
    resolve_matter,
)
from mootloop.web.api.sse import sse_run_events
from mootloop.web.security import AccessPrincipal

router = APIRouter()

# Annotated dependency aliases (avoids Depends()-in-default; the FastAPI idiom).
Vault = Annotated[Path, Depends(resolve_matter)]
Principal = Annotated[AccessPrincipal, Depends(require_access)]
Registry = Annotated[MatterRegistry, Depends(get_registry)]
Csrf = Annotated[None, Depends(require_csrf)]
Internal = Annotated[None, Depends(require_internal)]
QueueDep = Annotated[WorkQueue, Depends(get_queue)]
Signer = Annotated[link_svc.LinkSigner, Depends(get_link_signer)]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# --- health (unauthenticated liveness) --------------------------------------


@router.get("/health")
def health() -> dict[str, str]:
    """Unauthenticated liveness probe — no Access/Internal guard, no matter data.

    A GET, so the write-only `RateLimitMiddleware` never throttles it; it carries only
    the static app version so the container HEALTHCHECK and Coolify can probe readiness
    without a valid Cloudflare Access token."""
    return {"status": "ok", "version": mootloop.__version__}


def _audit_dep(action: str) -> Callable[..., None]:
    """A per-route dependency that records one access-audit entry for the hit.

    Depends on `require_access` and `resolve_matter`, so it runs only after the
    caller is authenticated and the matter resolves — and it records the verified
    email as the actor and the request path as the resource.
    """

    def _record(
        request: Request,
        matter_id: str,
        principal: Principal,
        vault: Vault,
    ) -> None:
        audit.append(
            vault,
            actor=principal.email,
            action=action,
            matter_id=matter_id,
            resource=request.url.path,
        )

    return _record


def _runs_for(vault: Path) -> list[models.RunSummary]:
    runs_dir = safe_vault_path(vault, "runs")
    if not runs_dir.is_dir():
        return []
    summaries: list[models.RunSummary] = []
    for child in sorted(runs_dir.iterdir()):
        if not child.is_dir():
            continue
        state = load_state(vault, child.name)
        summaries.append(
            models.RunSummary(
                run_id=child.name,
                status=state.status,
                mode=state.mode,
                current_stage=state.current_stage,
                task=state.task,
                total_spend_usd=state.total_spend_usd,
            )
        )
    return summaries


# --- CSRF token -------------------------------------------------------------


@router.get("/api/csrf")
def get_csrf(
    request: Request,
    response: Response,
    _principal: Principal,
) -> models.CsrfToken:
    return models.CsrfToken(csrf_token=issue_csrf_token(request, response))


# --- matter + run listing (read) --------------------------------------------


@router.get("/api/matters")
def list_matters(
    _principal: Principal,
    registry: Registry,
) -> list[MatterSummary]:
    return registry.list_matters()


@router.get("/api/matters/{matter_id}/runs")
def list_runs(
    matter_id: str,
    _principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("list_runs"))],
) -> list[models.RunSummary]:
    return _runs_for(vault)


# --- single-run read views (cockpit + inbox; Access + audited) --------------


@router.get("/api/matters/{matter_id}/runs/{run_id}")
def get_run(
    matter_id: str,
    run_id: str,
    _principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("run_status"))],
) -> models.RunStatusSummary:
    return readers.run_status_summary(vault, run_id)


@router.get("/api/matters/{matter_id}/runs/{run_id}/gates")
def get_run_gates(
    matter_id: str,
    run_id: str,
    _principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("run_gates"))],
) -> models.GateLedgerResponse:
    return readers.gate_ledger_response(vault, run_id)


@router.get("/api/matters/{matter_id}/runs/{run_id}/decisions")
def get_run_decisions(
    matter_id: str,
    run_id: str,
    _principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("run_decisions"))],
) -> models.DecisionsResponse:
    return readers.decisions_response(vault, run_id)


@router.get("/api/matters/{matter_id}/runs/{run_id}/requests")
def get_run_requests(
    matter_id: str,
    run_id: str,
    _principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("run_requests"))],
) -> models.RequestsResponse:
    return readers.requests_response(vault, run_id)


# --- run lifecycle writes (start / continue / raise-cap; Access + CSRF) ------


@router.post("/api/matters/{matter_id}/runs")
def start_run(
    matter_id: str,
    body: models.StartRunRequest,
    principal: Principal,
    vault: Vault,
    queue: QueueDep,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("run_start"))],
) -> models.RunStatusSummary:
    run_id = orchestrator.start_run(
        vault, body.task, _now_iso(), mode=body.mode, task_spec_id=body.task_spec_id
    )
    # Feed the driver queue so the worker actually picks the run up. Without this the
    # run is created but never executes (both FE-7 runs were enqueued operationally).
    queue.enqueue(
        WorkItem.create(
            lane="run",
            matter_id=matter_id,
            run_id=run_id,
            kind="run_turn",
            now=datetime.now(UTC),
        )
    )
    return readers.run_status_summary(vault, run_id)


@router.post("/api/matters/{matter_id}/runs/{run_id}/continue")
def continue_run(
    matter_id: str,
    run_id: str,
    principal: Principal,
    vault: Vault,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("run_continue"))],
) -> models.RunActionResponse:
    """Clear a gated-mode checkpoint so the run resumes (mirrors ``mootloop run
    continue``; the ``/resume`` route covers operator-paused runs)."""
    orchestrator.continue_run(vault, run_id)
    return _run_action(vault, run_id, "run_continued")


@router.post("/api/matters/{matter_id}/runs/{run_id}/raise-cap")
def raise_cap(
    matter_id: str,
    run_id: str,
    body: models.RaiseCapRequest,
    principal: Principal,
    vault: Vault,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("run_raise_cap"))],
) -> models.RunActionResponse:
    """Raise a capped run's hard budget cap (absolute ``to_usd`` or ``delta_usd`` over
    the current effective cap), reopening it for resumption (plan D5)."""
    if body.to_usd is not None:
        to_usd = body.to_usd
    else:
        current = readers.effective_cap(vault, load_state(vault, run_id))
        if current is None:
            raise OrchestratorError(
                f"run {run_id!r} has no cap to increment; pass an absolute `to_usd`"
            )
        assert body.delta_usd is not None  # guaranteed by the request validator
        to_usd = current + body.delta_usd
    orchestrator.raise_cap(vault, run_id, to_usd)
    return _run_action(vault, run_id, "cap_raised")


# --- decision resolve (write; typed 409 on lock contention) -----------------


@router.post("/api/matters/{matter_id}/runs/{run_id}/decisions/{decision_id}/resolve")
def resolve_decision(
    matter_id: str,
    run_id: str,
    decision_id: str,
    body: models.ResolveRequest,
    principal: Principal,
    vault: Vault,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("resolve"))],
) -> models.ResolveResponse:
    decision = decisions_svc.resolve(
        vault,
        run_id,
        decision_id,
        body.action,
        body.chosen_key,
        body.note,
        principal.email,
        "human",
        _now_iso(),
    )
    return models.ResolveResponse(decision=decision)


# --- attestation (write) ----------------------------------------------------


@router.post("/api/matters/{matter_id}/runs/{run_id}/attest")
def attest_run(
    matter_id: str,
    run_id: str,
    principal: Principal,
    vault: Vault,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("attest"))],
) -> models.AttestResponse:
    attestation = attest_svc.attest(vault, run_id, principal.email, _now_iso())
    return models.AttestResponse(attestation=attestation)


# --- begin-task on-ramp: freeform lane + TaskSpec listing -------------------


@router.post("/api/matters/{matter_id}/tasks/freeform")
def create_freeform_task(
    matter_id: str,
    body: models.FreeformTaskRequest,
    principal: Principal,
    vault: Vault,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("task_freeform"))],
) -> models.TaskSpecResponse:
    """Resolve free-text intent to a TaskSpec (deterministic v1; unmapped -> ``task=None``,
    recorded but not runnable). Persists append-only at ``tasks/specs.jsonl`` (plan FE-2.5)."""
    spec = taskspec_svc.create_freeform(vault, matter_id, body.intent_text, _now_iso())
    return models.TaskSpecResponse(task_spec=spec, runnable=spec.runnable)


@router.get("/api/matters/{matter_id}/tasks")
def list_tasks(
    matter_id: str,
    _principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("tasks_list"))],
) -> models.TaskSpecsResponse:
    """List the matter's recorded TaskSpecs (all lanes, append order)."""
    return models.TaskSpecsResponse(specs=taskspec_svc.list_specs(vault))


# --- run pause / resume (human; Access + CSRF + audited) --------------------


def _run_action(vault: Path, run_id: str, kind: str) -> models.RunActionResponse:
    state = load_state(vault, run_id)
    return models.RunActionResponse(kind=kind, run_id=run_id, status=state.status)  # type: ignore[arg-type]


@router.post("/api/matters/{matter_id}/runs/{run_id}/pause")
def pause_run_human(
    matter_id: str,
    run_id: str,
    principal: Principal,
    vault: Vault,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("pause"))],
) -> models.RunActionResponse:
    orchestrator.pause_run(vault, run_id, reason="manual")
    return _run_action(vault, run_id, "run_paused")


@router.post("/api/matters/{matter_id}/runs/{run_id}/resume")
def resume_run_human(
    matter_id: str,
    run_id: str,
    principal: Principal,
    vault: Vault,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("resume"))],
) -> models.RunActionResponse:
    orchestrator.resume_run(vault, run_id)
    return _run_action(vault, run_id, "run_resumed")


# --- live run stream (SSE; Access + audited) --------------------------------


@router.get("/api/matters/{matter_id}/runs/{run_id}/stream")
def stream_run(
    matter_id: str,
    run_id: str,
    principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("stream"))],
) -> StreamingResponse:
    """Tail the run's journal as Server-Sent Events until it is terminal (plan FE-1).

    A GET, so `RateLimitMiddleware` (write-only) never throttles it."""
    return StreamingResponse(sse_run_events(vault, run_id), media_type="text/event-stream")


# --- export: deliverables + signed download links (Access + audited) --------


@router.get("/api/matters/{matter_id}/runs/{run_id}/deliverables")
def list_deliverables(
    matter_id: str,
    run_id: str,
    _principal: Principal,
    vault: Vault,
    _audited: Annotated[None, Depends(_audit_dep("deliverables_list"))],
) -> models.DeliverablesResponse:
    """List a run's deliverables with DRAFT/clean state and per-file downloadability
    (clean files are downloadable only once the run is export-ready; plan P-37)."""
    from mootloop import gate_ledger

    ready, _blockers = gate_ledger.export_ready(vault, run_id)
    entries = link_svc.list_deliverables(vault, run_id)
    infos = [
        models.DeliverableInfo(
            name=e.name,
            size_bytes=e.size_bytes,
            is_draft=e.is_draft,
            requires_export_ready=e.requires_export_ready,
            downloadable=(ready if e.requires_export_ready else True),
        )
        for e in entries
    ]
    return models.DeliverablesResponse(run_id=run_id, export_ready=ready, deliverables=infos)


@router.post("/api/matters/{matter_id}/runs/{run_id}/deliverables/{name:path}/link")
def mint_download_link(
    matter_id: str,
    run_id: str,
    name: str,
    principal: Principal,
    vault: Vault,
    signer: Signer,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("deliverable_link"))],
) -> models.SignedLinkResponse:
    """Mint a short-expiry signed link for one deliverable. A clean (non-DRAFT) file
    that is not export-ready yields a typed 403 (``export_not_ready``); DRAFT files are
    always linkable (plan FD-7 / P-37)."""
    link = link_svc.mint_link(vault, matter_id, run_id, name, _now_iso(), signer)
    return models.SignedLinkResponse(
        run_id=run_id,
        doc=link.doc,
        url=link.url,
        token=link.token,
        is_draft=link.is_draft,
        expires_at=link.expires_at,
    )


@router.get("/api/download")
def download_deliverable(
    token: str,
    principal: Principal,
    registry: Registry,
    signer: Signer,
) -> FileResponse:
    """Validate a signed link, AUDIT-APPEND FIRST (fail closed), then stream the file.

    Not matter-scoped in the path — the token carries the (matter, run, deliverable)
    audience. The access audit MUST record before a byte streams: if the audit write
    fails, `AuditWriteError` propagates (500) and nothing is served (plan FD-3)."""
    claims = link_svc.validate_token(token, _now_iso(), signer)
    vault = registry.resolve(claims.matter_id)
    path = link_svc.resolve_for_download(vault, claims)
    # Fail closed: the download is not considered served unless it is durably recorded.
    audit.record_download_audit(
        vault, actor=principal.email, matter_id=claims.matter_id, resource=claims.doc
    )
    return FileResponse(path, filename=Path(claims.doc).name)


# --- driver-only surface (InternalAuth) -------------------------------------


@router.post("/internal/matters/{matter_id}/runs/{run_id}/pause")
def pause_run_internal(
    matter_id: str,
    run_id: str,
    body: models.PauseRequest,
    _internal: Internal,
    vault: Vault,
) -> models.RunActionResponse:
    orchestrator.pause_run(vault, run_id, reason=body.reason or "manual")
    return _run_action(vault, run_id, "run_paused")


@router.post("/internal/matters/{matter_id}/runs/{run_id}/resume")
def resume_run_internal(
    matter_id: str,
    run_id: str,
    _internal: Internal,
    vault: Vault,
) -> models.RunActionResponse:
    orchestrator.resume_run(vault, run_id)
    return _run_action(vault, run_id, "run_resumed")


@router.get("/internal/queue/next")
def internal_queue_next(
    worker_id: str,
    queue: QueueDep,
    _internal: Internal,
) -> Response:
    """Claim the next work item for ``worker_id`` (or 204 when the queue is idle)."""
    item = queue.claim(worker_id, datetime.now(UTC), visibility_timeout_s=300.0)
    if item is None:
        return Response(status_code=204)
    return JSONResponse(content=item.model_dump(mode="json"))


@router.get("/internal/ping")
def internal_ping(_internal: Internal) -> dict[str, str]:
    """Liveness probe for the driver/BFF internal path — demonstrates the internal
    auth guard is wired and introspectable; carries no matter data."""
    return {"status": "ok"}


# Re-export for the factory's introspection convenience.
route_auth_kinds = deps.route_auth_kinds
