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

from mootloop import attest as attest_svc
from mootloop import decisions as decisions_svc
from mootloop.journal import load_state
from mootloop.models.matters import MatterSummary
from mootloop.registry import MatterRegistry
from mootloop.vault import safe_vault_path
from mootloop.web import audit
from mootloop.web.api import deps, models
from mootloop.web.api.deps import (
    get_registry,
    issue_csrf_token,
    require_access,
    require_csrf,
    require_internal,
    resolve_matter,
)
from mootloop.web.security import AccessPrincipal

router = APIRouter()

# Annotated dependency aliases (avoids Depends()-in-default; the FastAPI idiom).
Vault = Annotated[Path, Depends(resolve_matter)]
Principal = Annotated[AccessPrincipal, Depends(require_access)]
Registry = Annotated[MatterRegistry, Depends(get_registry)]
Csrf = Annotated[None, Depends(require_csrf)]
Internal = Annotated[None, Depends(require_internal)]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _audit_dep(action: str) -> Callable[..., None]:
    """A per-route dependency that records one access-audit entry for the hit.

    Depends on `require_access` and `resolve_matter`, so it runs only after the
    caller is authenticated and the matter resolves — and it records the verified
    email as the actor and the request path as the resource.
    """

    def _record(
        request: Request,
        matter_id: str,
        vault: Vault,
        principal: Principal,
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
    vault: Vault,
    _principal: Principal,
    _audited: Annotated[None, Depends(_audit_dep("list_runs"))],
) -> list[models.RunSummary]:
    return _runs_for(vault)


# --- decision resolve (write; typed 409 on lock contention) -----------------


@router.post("/api/matters/{matter_id}/runs/{run_id}/decisions/{decision_id}/resolve")
def resolve_decision(
    matter_id: str,
    run_id: str,
    decision_id: str,
    body: models.ResolveRequest,
    vault: Vault,
    principal: Principal,
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
    vault: Vault,
    principal: Principal,
    _csrf: Csrf,
    _audited: Annotated[None, Depends(_audit_dep("attest"))],
) -> models.AttestResponse:
    attestation = attest_svc.attest(vault, run_id, principal.email, _now_iso())
    return models.AttestResponse(attestation=attestation)


# --- driver-only surface (InternalAuth) -------------------------------------


@router.get("/internal/ping")
def internal_ping(_internal: Internal) -> dict[str, str]:
    """Liveness probe for the driver/BFF internal path — demonstrates the internal
    auth guard is wired and introspectable; carries no matter data."""
    return {"status": "ok"}


# Re-export for the factory's introspection convenience.
route_auth_kinds = deps.route_auth_kinds
