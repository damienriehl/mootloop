"""Structural invariants for the write-tier matter API (phase FE-0, unit 3).

1. The `mootloop.web.api` package NEVER imports `mootloop.web.app` — the write tier
   and the read-only demo stay separate (AST scan of every api source file).
2. Every MUTATING route (POST/PUT/PATCH/DELETE) declares an auth guard (Cloudflare
   Access or InternalAuth) — introspected off ``route.dependant``, never executed.
3. The access-audit chain folds == recompute: appended entries verify, and a tampered
   line breaks verification (fail-closed integrity).
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.routing import APIRoute

from mootloop.web import audit
from mootloop.web.api import create_matter_api, route_auth_kinds

_API_DIR = Path(__file__).resolve().parents[2] / "src" / "mootloop" / "web" / "api"
_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_AUTH_KINDS = {"cf_access", "internal"}


def _iter_api_routes(app: FastAPI) -> Iterator[APIRoute]:
    """Yield every `APIRoute`, unwrapping the lazy ``_IncludedRouter`` FastAPI wraps an
    ``include_router`` call in (its `original_router` holds the real routes)."""
    for route in app.routes:
        if isinstance(route, APIRoute):
            yield route
        original = getattr(route, "original_router", None)
        if original is not None:
            for sub in original.routes:
                if isinstance(sub, APIRoute):
                    yield sub


# --- 1. api never imports web.app -------------------------------------------


def _references_web_app(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "mootloop.web.app":
                return True
            if module == "mootloop.web" and any(a.name == "app" for a in node.names):
                return True
        elif isinstance(node, ast.Import):
            if any(alias.name == "mootloop.web.app" for alias in node.names):
                return True
    return False


def test_api_never_imports_web_app() -> None:
    offenders: list[str] = []
    for source in sorted(_API_DIR.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"))
        if _references_web_app(tree):
            offenders.append(source.name)
    assert not offenders, f"api package imports mootloop.web.app in: {offenders}"


# --- 2. every mutating route is auth-guarded --------------------------------


def test_every_mutating_route_declares_auth() -> None:
    app = create_matter_api()
    unguarded: list[str] = []
    for route in _iter_api_routes(app):
        methods = route.methods or set()
        if not (methods & _MUTATING):
            continue
        kinds = route_auth_kinds(route)
        if not (kinds & _AUTH_KINDS):
            unguarded.append(f"{sorted(methods)} {route.path}")
    assert not unguarded, f"mutating routes without an auth guard: {unguarded}"


def test_at_least_one_mutating_route_exists() -> None:
    """Guard against the auth test passing vacuously if the router is empty."""
    app = create_matter_api()
    mutating = [r for r in _iter_api_routes(app) if (r.methods or set()) & _MUTATING]
    assert mutating, "expected at least one mutating route in the matter API"


# Routes that are deliberately reachable without an auth guard. Anything else — read
# or write — must declare one. Adding a path here is the explicit, reviewable act.
_UNAUTHENTICATED_ALLOWLIST = {"/health"}


def test_every_route_declares_auth_unless_explicitly_allowlisted() -> None:
    """The mutating-only check above cannot see a NEW read route that serves matter
    data. `/api/download` streams privileged work product over GET, and the SSE stream
    tails a live run's journal — a future GET added without a `Principal` would have
    passed every structural test in this file."""
    app = create_matter_api()
    unguarded: list[str] = []
    for route in _iter_api_routes(app):
        if route.path in _UNAUTHENTICATED_ALLOWLIST:
            continue
        if not (route_auth_kinds(route) & _AUTH_KINDS):
            unguarded.append(f"{sorted(route.methods or set())} {route.path}")
    assert not unguarded, f"routes without an auth guard: {unguarded}"


def test_the_unauthenticated_allowlist_is_real_and_minimal() -> None:
    """Every allowlisted path must exist (a typo would silently exempt nothing — or,
    after a rename, silently exempt the wrong thing)."""
    paths = {r.path for r in _iter_api_routes(create_matter_api())}
    assert paths >= _UNAUTHENTICATED_ALLOWLIST, _UNAUTHENTICATED_ALLOWLIST - paths


def test_matter_scoped_read_routes_are_audited() -> None:
    """Every matter-scoped route records an access-audit entry. FD-3 makes the audit
    the fail-closed precondition for serving matter data, so a route that resolves a
    matter but records nothing serves privileged content off the books."""
    app = create_matter_api()
    unaudited: list[str] = []
    for route in _iter_api_routes(app):
        if "{matter_id}" not in route.path:
            continue
        names = {
            getattr(dep.call, "__qualname__", "")
            for dep in _walk_dependencies(route)
            if dep.call is not None
        }
        if not any("_audit_dep" in name or "_record" in name for name in names):
            unaudited.append(f"{sorted(route.methods or set())} {route.path}")
    assert not unaudited, f"matter-scoped routes with no access audit: {unaudited}"


def _walk_dependencies(route: APIRoute) -> list[object]:
    out: list[object] = []
    stack: list[object] = [route.dependant]
    seen: set[int] = set()
    while stack:
        node = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        out.append(node)
        stack.extend(getattr(node, "dependencies", []))
    return out


# --- 3. access-audit chain: fold == recompute, tamper breaks it -------------


def test_audit_chain_folds_and_recomputes(tmp_path: Path) -> None:
    for i in range(4):
        audit.append(
            tmp_path,
            actor="attorney@example.com",
            action="view",
            matter_id="acme-v-widgets",
            resource=f"/api/matters/acme-v-widgets/runs?i={i}",
            ts=f"2026-07-12T00:0{i}:00+00:00",
        )
    assert audit.verify_chain(tmp_path) is True


def test_audit_chain_detects_tamper(tmp_path: Path) -> None:
    for i in range(3):
        audit.append(
            tmp_path,
            actor="attorney@example.com",
            action="view",
            matter_id="acme-v-widgets",
            resource="/api/matters/acme-v-widgets/runs",
            ts=f"2026-07-12T00:0{i}:00+00:00",
        )
    path = audit.audit_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = lines[1].replace('"action":"view"', '"action":"download"')
    assert tampered != lines[1], "expected the middle entry to contain the edited field"
    lines[1] = tampered
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert audit.verify_chain(tmp_path) is False
