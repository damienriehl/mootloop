"""Keep the agent/human capability inventory honest as surfaces evolve."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import FastAPI
from fastapi.routing import APIRoute
from typer.main import get_command

from mootloop.cli import app as cli_app
from mootloop.web.api import create_matter_api

_ROOT = Path(__file__).resolve().parents[2]
_MATRIX = _ROOT / "docs" / "capability-matrix.yaml"
_STATUSES = {"implemented", "planned"}
_ACTOR_POLICIES = {
    "authenticated-read",
    "authenticated-human",
    "hard-human",
    "policy-delegable",
}


def _load() -> list[dict[str, Any]]:
    raw = yaml.safe_load(_MATRIX.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    assert raw.get("schema_version") == "1.0"
    capabilities = raw.get("capabilities")
    assert isinstance(capabilities, list)
    return capabilities


def _api_routes(app: FastAPI) -> list[APIRoute]:
    routes: list[APIRoute] = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            routes.append(route)
        original = getattr(route, "original_router", None)
        if original is not None:
            routes.extend(item for item in original.routes if isinstance(item, APIRoute))
    return routes


def _cli_paths(group: Any, prefix: tuple[str, ...] = ()) -> set[str]:
    paths: set[str] = set()
    for name, command in group.commands.items():
        path = (*prefix, name)
        paths.add(" ".join(path))
        if hasattr(command, "commands"):
            paths.update(_cli_paths(command, path))
    return paths


def test_capability_rows_are_unique_and_explicit() -> None:
    rows = _load()
    ids = [row.get("id") for row in rows]
    assert len(ids) == len(set(ids))
    assert {f"FD7-{number:02d}" for number in range(1, 18)} <= set(ids)
    for row in rows:
        assert row.get("status") in _STATUSES
        assert row.get("actor_policy") in _ACTOR_POLICIES
        if row["status"] == "planned":
            assert row.get("owner"), row["id"]


def test_implemented_rows_reference_live_surfaces_and_evidence() -> None:
    routes = {
        (method, route.path)
        for route in _api_routes(create_matter_api())
        for method in route.methods or set()
    }
    cli_paths = _cli_paths(get_command(cli_app))
    for row in _load():
        if row["status"] != "implemented":
            continue
        service_path, _, symbol = row["service"].partition(":")
        service = _ROOT / service_path
        assert service.is_file(), row["id"]
        service_text = service.read_text(encoding="utf-8")
        assert symbol and symbol.rsplit(".", 1)[-1] in service_text, row["id"]
        invariant = row.get("invariant")
        if invariant is not None:
            assert (_ROOT / invariant).is_file(), row["id"]
        else:
            assert row.get("cli") in cli_paths, row["id"]
            api = row.get("api")
            assert isinstance(api, dict)
            assert (api["method"], api["path"]) in routes, row["id"]
        ui = row.get("ui")
        assert ui is None or (_ROOT / ui).is_file(), row["id"]
        evidence = row.get("evidence")
        assert evidence and all((_ROOT / path).is_file() for path in evidence), row["id"]
