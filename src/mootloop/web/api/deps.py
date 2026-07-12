"""Dependencies for the write-tier matter API: injectable auth/registry providers,
the two auth guards (Cloudflare Access for humans, InternalAuth for the driver/BFF),
per-matter resolution, and CSRF double-submit.

The provider functions (`get_verifier`, `get_internal_auth`, `get_registry`) are the
``dependency_overrides`` seams tests stub. Each auth guard carries an introspection
marker (`AUTH_ATTR`) so a route's declared auth can be read off ``route.dependant``
without executing it — the invariant test uses `route_auth_kinds`.
"""

from __future__ import annotations

import hmac
import secrets as secrets_mod
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, Response

from mootloop.errors import AccessAuthError, InternalAuthError
from mootloop.registry import MatterRegistry
from mootloop.web.security import AccessPrincipal, CfAccessVerifier, InternalAuth

# Marker attribute stamped on each auth guard; read back via route introspection.
AUTH_ATTR = "__mootloop_auth__"

CF_ACCESS_HEADER = "cf-access-jwt-assertion"
CSRF_COOKIE = "mootloop_csrf"
CSRF_HEADER = "x-csrf-token"
_CSRF_MAX_AGE = 86_400


# --- injectable providers (dependency_overrides seams) ----------------------


def get_registry() -> MatterRegistry:
    """The matters-root registry (env-configured). Overridden in tests."""
    return MatterRegistry()


def get_verifier() -> CfAccessVerifier:
    """The Cloudflare Access JWT verifier (env-configured, fail-closed)."""
    return CfAccessVerifier.from_env()


def get_internal_auth() -> InternalAuth:
    """The driver/BFF internal-secret checker (fail-closed)."""
    return InternalAuth.from_env()


# --- auth guards (introspectable) -------------------------------------------


def require_access(
    request: Request,
    verifier: Annotated[CfAccessVerifier, Depends(get_verifier)],
) -> AccessPrincipal:
    """Verify the ``Cf-Access-Jwt-Assertion`` header; the verified principal or 401."""
    token = request.headers.get(CF_ACCESS_HEADER)
    try:
        return verifier.verify(token)
    except AccessAuthError as exc:
        raise HTTPException(status_code=401, detail="access denied") from exc


def require_internal(
    request: Request,
    internal: Annotated[InternalAuth, Depends(get_internal_auth)],
) -> None:
    """Verify the driver/BFF internal secret header; 401 on any mismatch."""
    presented = request.headers.get(InternalAuth.HEADER)
    try:
        internal.verify(presented)
    except InternalAuthError as exc:
        raise HTTPException(status_code=401, detail="internal auth failed") from exc


setattr(require_access, AUTH_ATTR, "cf_access")
setattr(require_internal, AUTH_ATTR, "internal")


# --- per-matter resolution --------------------------------------------------


def resolve_matter(
    matter_id: str,
    registry: Annotated[MatterRegistry, Depends(get_registry)],
) -> Path:
    """Resolve an untrusted ``matter_id`` to its vault via the registry (fail-closed).

    Charset and realpath-containment breaches raise `VaultBoundaryError` (-> 400) and
    an unknown matter raises `MatterNotFoundError` (-> 404); both are mapped centrally.
    """
    return registry.resolve(matter_id)


# --- CSRF double-submit -----------------------------------------------------


def require_csrf(request: Request) -> None:
    """Fail closed unless the ``X-CSRF-Token`` header equals the CSRF cookie."""
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=403, detail="CSRF token missing or mismatched")


def issue_csrf_token(request: Request, response: Response) -> str:
    """Mint a CSRF token, set it as the double-submit cookie, and return it.

    ``Secure`` follows the request scheme so the cookie survives http test clients
    while staying Secure in production; ``SameSite=Strict`` and a readable (non-
    HttpOnly) cookie are the double-submit contract (the client echoes it as a header).
    """
    token = secrets_mod.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=_CSRF_MAX_AGE,
        secure=request.url.scheme == "https",
        httponly=False,
        samesite="strict",
        path="/",
    )
    return token


# --- route auth introspection -----------------------------------------------


def route_auth_kinds(route: object) -> set[str]:
    """The set of auth kinds (``cf_access`` / ``internal``) a route declares.

    Walks the route's dependant tree and collects the `AUTH_ATTR` marker off every
    dependency callable — so an invariant test can assert each mutating route is
    guarded without issuing a request.
    """
    kinds: set[str] = set()
    dependant = getattr(route, "dependant", None)
    if dependant is None:
        return kinds
    stack: list[Any] = [dependant]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        marker = getattr(getattr(node, "call", None), AUTH_ATTR, None)
        if isinstance(marker, str):
            kinds.add(marker)
        stack.extend(getattr(node, "dependencies", []))
    return kinds
