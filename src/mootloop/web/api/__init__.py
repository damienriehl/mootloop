"""Hosted write-tier matter API (plan "Backend extension", phase FE-0).

`create_matter_api` builds a FastAPI app SEPARATE from the read-only demo
(`mootloop.web.app`) — this package never imports that module (an invariant test
enforces it). It layers the FE-0 perimeter over the existing tested services:
Cloudflare Access on human routes, InternalAuth on driver routes, CSRF double-submit
on mutating routes, the pure-ASGI `RateLimitMiddleware` on writes, and a hash-chained
access audit on every matter-data hit. Service errors surface as typed HTTP responses
(the lock-contention 409 is a structured body).
"""

from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import mootloop
from mootloop.errors import (
    AccessAuthError,
    AttestationBlockedError,
    AuditWriteError,
    DecisionError,
    InternalAuthError,
    LockHeldError,
    MatterNotFoundError,
    VaultBoundaryError,
)
from mootloop.web.api import models, routes
from mootloop.web.api.deps import route_auth_kinds
from mootloop.web.security import RateLimitMiddleware

__all__ = ["create_matter_api", "route_auth_kinds"]

_RATE_CAPACITY_ENV = "MOOTLOOP_RATE_CAPACITY"
_RATE_REFILL_ENV = "MOOTLOOP_RATE_REFILL_PER_SEC"


def _install_error_handlers(app: FastAPI) -> None:
    """Map service/auth exceptions to typed HTTP responses (fail-closed, no leakage)."""

    @app.exception_handler(LockHeldError)
    async def _lock_held(request: Request, exc: LockHeldError) -> JSONResponse:
        body = models.LockContentionBody(detail=str(exc))
        return JSONResponse(status_code=409, content=body.model_dump())

    @app.exception_handler(AttestationBlockedError)
    async def _attest_blocked(request: Request, exc: AttestationBlockedError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content={"error": "attestation_blocked", "detail": str(exc)}
        )

    @app.exception_handler(DecisionError)
    async def _decision(request: Request, exc: DecisionError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "decision", "detail": str(exc)})

    @app.exception_handler(MatterNotFoundError)
    async def _not_found(request: Request, exc: MatterNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": "matter_not_found"})

    @app.exception_handler(VaultBoundaryError)
    async def _boundary(request: Request, exc: VaultBoundaryError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": "invalid_matter_id"})

    @app.exception_handler(AccessAuthError)
    async def _access(request: Request, exc: AccessAuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "access_denied"})

    @app.exception_handler(InternalAuthError)
    async def _internal(request: Request, exc: InternalAuthError) -> JSONResponse:
        return JSONResponse(status_code=401, content={"error": "internal_auth_failed"})

    @app.exception_handler(AuditWriteError)
    async def _audit(request: Request, exc: AuditWriteError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"error": "audit_write_failed"})


def create_matter_api() -> FastAPI:
    """Build the write-tier matter API app (separate from the read-only demo)."""
    app = FastAPI(
        title="MootLoop matter API",
        description="Hosted write-tier API over per-matter vaults (Access-gated).",
        version=mootloop.__version__,
    )
    _install_error_handlers(app)
    app.include_router(routes.router)
    # Pure-ASGI token bucket on write methods (unit 2). Not BaseHTTPMiddleware — that
    # would break SSE. Env overrides mirror `RateLimitMiddleware.from_env`.
    app.add_middleware(
        RateLimitMiddleware,
        capacity=float(os.environ.get(_RATE_CAPACITY_ENV, 20.0)),
        refill_per_sec=float(os.environ.get(_RATE_REFILL_ENV, 2.0)),
    )
    return app
