"""Public, read-only API over reviewed projections. No vault or provider imports."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pydantic import JsonValue
from starlette.middleware.base import RequestResponseEndpoint

import mootloop
from mootloop.models.demo import DemoCatalog, DemoSnapshot, LegacyProjection
from mootloop.web.catalog import PublicationError, PublicCatalog, identity

PUBLIC_ENV = "MOOTLOOP_PUBLIC_ROOT"
DEFAULT_PUBLIC_ROOT = "/app/public"
_STATIC_DIR = Path(__file__).parent / "static"
app = FastAPI(
    title="MootLoop demo library",
    description="Prepared, read-only legal demonstrations.",
    version=mootloop.__version__,
)


def _reader() -> PublicCatalog:
    return PublicCatalog(Path(os.environ.get(PUBLIC_ENV, DEFAULT_PUBLIC_ROOT)))


@app.middleware("http")
async def public_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(PublicationError)
async def unavailable(request: Request, exc: PublicationError) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "detail": "The reviewed demo release is unavailable. Try the library again later."
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": mootloop.__version__}


@app.get("/ready")
def ready() -> dict[str, str | int]:
    catalog = _reader().ready()
    return {"status": "ready", "release_id": catalog.release_id, "demos": len(catalog.entries)}


@app.get("/api/demos")
def demos() -> DemoCatalog:
    return _reader().catalog()


def _revision(demo_id: str, revision: str | None = None) -> str:
    try:
        identity(demo_id)
        if revision is not None:
            identity(revision)
    except PublicationError as exc:
        raise HTTPException(404, "Unknown demo or revision") from exc
    entry = next((e for e in _reader().catalog().entries if e.descriptor.demo_id == demo_id), None)
    if entry is None or revision is not None and entry.revision != revision:
        raise HTTPException(404, "Unknown or withdrawn demo revision")
    return entry.revision


@app.get("/api/demos/{demo_id}")
def current_demo(demo_id: str) -> DemoSnapshot:
    return _reader().snapshot(demo_id, _revision(demo_id))


@app.get("/api/demos/{demo_id}/{revision}")
def demo_revision(demo_id: str, revision: str) -> DemoSnapshot:
    return _reader().snapshot(demo_id, _revision(demo_id, revision))


@app.get("/api/demos/{demo_id}/{revision}/inputs")
def demo_inputs(demo_id: str, revision: str) -> Response:
    _revision(demo_id, revision)
    raw = _reader().bundle_bytes(demo_id, revision)
    return Response(
        raw,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{demo_id}-{revision}-inputs.json"'},
    )


def _legacy() -> LegacyProjection:
    return _reader().legacy()


@app.get("/api/requests/{request_id}/{view}")
def legacy_request(request_id: str, view: str) -> JsonValue:
    if view not in {"turns", "panel", "response"}:
        raise HTTPException(404, "Unknown request view")
    key = f"requests/{request_id}/{view}"
    projection = _legacy()
    if key not in projection.responses:
        raise HTTPException(404, "Unknown legacy request")
    return projection.responses[key]


@app.get("/api/deliverables/{name:path}")
def legacy_deliverable(name: str) -> PlainTextResponse:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or any(part in ("", ".", "..") for part in name.split("/"))
    ):
        raise HTTPException(400, "Invalid deliverable name")
    projection = _legacy()
    if name not in projection.deliverables:
        raise HTTPException(404, "Unknown legacy deliverable")
    media = "application/json" if name.endswith(".json") else "text/markdown"
    return PlainTextResponse(projection.deliverables[name], media_type=media)


@app.get("/api/{view}")
def legacy_view(view: str) -> JsonValue:
    if view not in {"matter", "run", "requests", "decisions", "gates", "deliverables", "sets"}:
        raise HTTPException(404, "Unknown legacy view")
    return _legacy().responses[view]


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(_STATIC_DIR / "home.html", media_type="text/html")


@app.get("/legacy", include_in_schema=False)
def legacy_page() -> FileResponse:
    return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")


@app.get("/demos/", include_in_schema=False)
@app.get("/demos/{demo_id}", include_in_schema=False)
def library_page(demo_id: str | None = None) -> FileResponse:
    return FileResponse(_STATIC_DIR / "library.html", media_type="text/html")


app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")
