"""Structural invariant: the Next.js BFF stays a thin, single-proxy boundary."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.invariant

_ROUTE_PATH = (
    Path(__file__).resolve().parents[2]
    / "frontend"
    / "app"
    / "api"
    / "[...path]"
    / "route.ts"
)
_API_ROOT = _ROUTE_PATH.parents[1]
_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
_IMPORT_SPECIFIER = re.compile(
    r"(?:\bfrom\s+|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)"
    r'["\'](?P<specifier>[^"\']+)["\']'
)
_METHOD_EXPORT = re.compile(
    r"^export\s+const\s+(?P<method>[A-Z]+)\s*=\s*(?P<delegate>[A-Za-z_$][\w$]*)\s*;",
    re.MULTILINE,
)
_NODE_BOUNDARY_ROOTS = {
    "child_process",
    "cluster",
    "fs",
    "os",
    "path",
    "process",
    "sqlite",
    "worker_threads",
}
_DATA_STORE_PACKAGES = {
    "@prisma/client",
    "@upstash/redis",
    "@vercel/postgres",
    "better-sqlite3",
    "drizzle-orm",
    "firebase-admin",
    "ioredis",
    "knex",
    "kysely",
    "level",
    "mongodb",
    "mongoose",
    "mysql",
    "mysql2",
    "pg",
    "postgres",
    "redis",
    "sequelize",
    "sqlite3",
}


@pytest.fixture(scope="module")
def route_source() -> str:
    return _ROUTE_PATH.read_text(encoding="utf-8")


def test_catch_all_is_the_only_frontend_api_route() -> None:
    routes = sorted(path.relative_to(_API_ROOT).as_posix() for path in _API_ROOT.rglob("route.ts"))
    assert routes == ["[...path]/route.ts"], (
        "every frontend API route must pass through the reviewed catch-all proxy; "
        f"found {routes}"
    )


def _package_root(specifier: str) -> str:
    if specifier.startswith("@"):
        return "/".join(specifier.split("/")[:2])
    return specifier.split("/", 1)[0]


def test_bff_imports_no_local_domain_or_server_state_modules(route_source: str) -> None:
    """The catch-all may use web proxy primitives, but never app or server state."""
    offenders: list[str] = []
    for match in _IMPORT_SPECIFIER.finditer(route_source):
        specifier = match.group("specifier")
        normalized = specifier.removeprefix("node:")
        package_root = _package_root(normalized)
        is_local = specifier.startswith((".", "@/", "~/"))
        is_domain = specifier == "mootloop" or specifier.startswith("mootloop/")
        is_node_boundary = package_root in _NODE_BOUNDARY_ROOTS
        is_data_store = package_root in _DATA_STORE_PACKAGES
        if is_local or is_domain or is_node_boundary or is_data_store:
            offenders.append(specifier)

    assert not offenders, (
        "BFF route imports local/domain, filesystem/process, or data-store modules: "
        f"{sorted(set(offenders))}"
    )


def test_every_http_method_delegates_to_the_single_proxy(route_source: str) -> None:
    definitions = re.findall(r"^async\s+function\s+proxy\s*\(", route_source, re.MULTILINE)
    assert len(definitions) == 1, "BFF route must define exactly one async proxy function"

    exports = {
        match.group("method"): match.group("delegate")
        for match in _METHOD_EXPORT.finditer(route_source)
    }
    assert set(exports) == _HTTP_METHODS, (
        "BFF route must export the complete HTTP method set; "
        f"missing={sorted(_HTTP_METHODS - set(exports))}, "
        f"extra={sorted(set(exports) - _HTTP_METHODS)}"
    )
    assert set(exports.values()) == {"proxy"}, (
        f"every exported HTTP method must delegate directly to proxy: {exports}"
    )
    assert re.search(r"\bfetch\s*\(", route_source), "proxy must forward requests with fetch"
