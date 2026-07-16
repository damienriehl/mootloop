"""Endpoint matrix for the write-tier matter API (unit 3 scaffold + unit 4 matrix).

Drives `create_matter_api` through the FastAPI TestClient with a STUBBED verifier
(via ``dependency_overrides``): the Access guard, the CSRF double-submit, the typed
409 lock-contention body, the attest happy path, real registry-backed run listing,
matter-not-found / invalid-id mapping, and the per-hit access-audit write. The
real-cryptography verifier path lives in ``test_api_real_verifier.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mootloop import orchestrator
from mootloop.engine.queue import Queue
from mootloop.errors import AccessAuthError, LockHeldError
from mootloop.models.attestations import Attestation
from mootloop.models.matter import MatterConfig
from mootloop.registry import MatterRegistry
from mootloop.web import audit
from mootloop.web.api import create_matter_api, routes
from mootloop.web.api.deps import get_internal_auth, get_queue, get_registry, get_verifier
from mootloop.web.security import AccessPrincipal, InternalAuth

_PRINCIPAL = AccessPrincipal(email="attorney@example.com", subject="sub-1", claims={})
_AUTH = {"cf-access-jwt-assertion": "good"}
_NOW_ISO = "2026-07-12T00:00:00+00:00"


class _StubVerifier:
    """Accepts the literal token ``"good"``; rejects everything else (fail-closed)."""

    def verify(self, token: str | None) -> AccessPrincipal:
        if token == "good":
            return _PRINCIPAL
        raise AccessAuthError("stub rejects token")


@pytest.fixture
def registry(tmp_path: Path, matter: MatterConfig) -> MatterRegistry:
    reg = MatterRegistry(root=tmp_path / "matters")
    reg.create(matter)
    return reg


@pytest.fixture
def queue(registry: MatterRegistry) -> Queue:
    return Queue(registry.root)


@pytest.fixture
def client(registry: MatterRegistry, queue: Queue) -> TestClient:
    app = create_matter_api()
    app.dependency_overrides[get_verifier] = _StubVerifier
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_queue] = lambda: queue
    return TestClient(app)


def _with_csrf(client: TestClient) -> dict[str, str]:
    """GET /api/csrf (sets the double-submit cookie on the client) + echo the token."""
    issued = client.get("/api/csrf", headers=_AUTH)
    assert issued.status_code == 200
    return {**_AUTH, "x-csrf-token": issued.json()["csrf_token"]}


def test_matters_requires_valid_access(client: TestClient) -> None:
    assert client.get("/api/matters").status_code == 401


# --- health: unauthenticated, no matter data --------------------------------


def test_health_ok_and_leaks_no_matter_data(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "version"}


def test_health_bypasses_access_guard_without_cf_config() -> None:
    """A fresh app with no CF_ACCESS_* env (matter routes would 401) still serves
    /health — proving the probe sits outside the Access-JWT and internal guards."""
    resp = TestClient(create_matter_api()).get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    # The Access guard is intact for matter routes on the same unconfigured app.
    assert TestClient(create_matter_api()).get("/api/matters").status_code == 401


def test_matters_lists_for_valid_access(client: TestClient, matter: MatterConfig) -> None:
    resp = client.get("/api/matters", headers={"cf-access-jwt-assertion": "good"})
    assert resp.status_code == 200
    payload = resp.json()
    assert [m["matter_id"] for m in payload] == [matter.matter_id]


def test_csrf_issued_and_required_on_mutation(client: TestClient, matter: MatterConfig) -> None:
    auth = {"cf-access-jwt-assertion": "good"}
    issued = client.get("/api/csrf", headers=auth)
    assert issued.status_code == 200
    assert issued.json()["csrf_token"]

    # A mutating route without the CSRF header fails closed (403), even authenticated.
    blocked = client.post(
        f"/api/matters/{matter.matter_id}/runs/r1/decisions/d1/resolve",
        headers=auth,
        json={"action": "approve"},
    )
    assert blocked.status_code == 403


# --- resolve: typed 409 on lock contention ----------------------------------


def test_resolve_returns_typed_409_on_lock_held(
    client: TestClient, matter: MatterConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_args: object, **_kw: object) -> None:
        raise LockHeldError("run lock is held by another writer")

    monkeypatch.setattr(routes.decisions_svc, "resolve", _raise)
    headers = _with_csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs/r1/decisions/d1/resolve",
        headers=headers,
        json={"action": "approve"},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "lock_held"
    assert body["retriable"] is True


# --- attest: happy path + audit write ---------------------------------------


def test_attest_happy_path_returns_envelope_and_audits(
    client: TestClient,
    registry: MatterRegistry,
    matter: MatterConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record = Attestation(
        attestation_id="att-r1-0000",
        run_id="r1",
        master_sha256="a" * 64,
        ledger_head_sha256="b" * 64,
        reviewer=_PRINCIPAL.email,
        attested_at=_NOW_ISO,
        valid=True,
    )
    monkeypatch.setattr(routes.attest_svc, "attest", lambda *a, **k: record)
    headers = _with_csrf(client)
    resp = client.post(f"/api/matters/{matter.matter_id}/runs/r1/attest", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "attested"
    assert body["attestation"]["reviewer"] == _PRINCIPAL.email
    assert body["attestation"]["valid"] is True

    vault = registry.resolve(matter.matter_id)
    assert audit.audit_path(vault).is_file()
    assert audit.verify_chain(vault) is True


# --- run listing over a real registry-backed vault --------------------------


def test_runs_listing_returns_started_run(
    client: TestClient, registry: MatterRegistry, matter: MatterConfig
) -> None:
    vault = registry.resolve(matter.matter_id)
    run_id = orchestrator.start_run(vault, "discovery-responses", _NOW_ISO)
    resp = client.get(f"/api/matters/{matter.matter_id}/runs", headers=_AUTH)
    assert resp.status_code == 200
    runs = resp.json()
    assert [r["run_id"] for r in runs] == [run_id]
    assert runs[0]["status"]


def test_start_run_enqueues_run_lane_work_item(
    client: TestClient, queue: Queue, matter: MatterConfig
) -> None:
    """POST /runs must feed the driver queue, not just create the run — otherwise the
    worker never picks it up (the hosted enqueue gap both FE-7 runs worked around)."""
    assert queue.snapshot() == []
    headers = _with_csrf(client)
    resp = client.post(
        f"/api/matters/{matter.matter_id}/runs",
        headers=headers,
        json={"task": "discovery-responses"},
    )
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]
    items = queue.snapshot()
    assert len(items) == 1
    item = items[0]
    assert item.lane == "run"
    assert item.kind == "run_turn"
    assert item.matter_id == matter.matter_id
    assert item.run_id == run_id


def test_runs_unknown_matter_returns_404(client: TestClient) -> None:
    resp = client.get("/api/matters/ghost-matter/runs", headers=_AUTH)
    assert resp.status_code == 404
    assert resp.json()["error"] == "matter_not_found"


def test_runs_invalid_matter_id_returns_400(client: TestClient) -> None:
    # Uppercase is charset-invalid -> VaultBoundaryError -> 400 (never a 404 probe).
    resp = client.get("/api/matters/UPPERCASE/runs", headers=_AUTH)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_matter_id"


# --- access audit is written on a matter-data route hit ---------------------


def test_matter_data_route_records_hash_chained_audit(
    client: TestClient, registry: MatterRegistry, matter: MatterConfig
) -> None:
    vault = registry.resolve(matter.matter_id)
    assert not audit.audit_path(vault).is_file()  # nothing recorded yet

    resp = client.get(f"/api/matters/{matter.matter_id}/runs", headers=_AUTH)
    assert resp.status_code == 200

    path = audit.audit_path(vault)
    entries = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(entries) == 1
    assert '"actor":"attorney@example.com"' in entries[0]
    assert audit.verify_chain(vault) is True


# --- FE-0 auth-before-resolve: no existence/charset oracle without auth ------
#
# The vault resolver must NEVER run before the auth guard. An unauthenticated
# caller must get 401 whether the matter exists, is unknown, or is charset-invalid
# — so existent and nonexistent matters are indistinguishable (no 404/400 oracle).

_NO_AUTH: dict[str, str] = {}  # the stub verifier rejects every non-"good" token


@pytest.fixture
def internal_client(registry: MatterRegistry) -> TestClient:
    """Client whose internal-secret guard is stubbed so we can assert secret-less 401s."""
    app = create_matter_api()
    app.dependency_overrides[get_verifier] = _StubVerifier
    app.dependency_overrides[get_registry] = lambda: registry
    app.dependency_overrides[get_internal_auth] = lambda: InternalAuth(secret="s3cr3t")
    return TestClient(app)


@pytest.mark.parametrize(
    "matter_id",
    [
        "ghost-matter",  # nonexistent -> would be 404 if the resolver ran first
        "UPPERCASE",  # charset-invalid -> would be 400 if the resolver ran first
    ],
)
def test_read_route_401_without_auth_regardless_of_matter(
    client: TestClient, matter_id: str
) -> None:
    resp = client.get(f"/api/matters/{matter_id}/runs", headers=_NO_AUTH)
    assert resp.status_code == 401


def test_read_route_401_without_auth_for_existing_matter(
    client: TestClient, matter: MatterConfig
) -> None:
    # An EXISTING matter is likewise 401 without auth — no distinction from the above.
    resp = client.get(f"/api/matters/{matter.matter_id}/runs", headers=_NO_AUTH)
    assert resp.status_code == 401


@pytest.mark.parametrize("matter_id", ["ghost-matter", "UPPERCASE"])
def test_mutating_route_401_without_auth_regardless_of_matter(
    client: TestClient, matter_id: str
) -> None:
    # POST with neither Access JWT nor CSRF fails on auth (401), never on resolve (404/400).
    resp = client.post(f"/api/matters/{matter_id}/runs", headers=_NO_AUTH, json={"task": "x"})
    assert resp.status_code == 401


def test_internal_route_401_without_secret_regardless_of_matter(
    internal_client: TestClient, matter: MatterConfig
) -> None:
    # No internal secret header -> 401 for a nonexistent, a charset-invalid, AND an
    # existing matter — the Internal guard runs before the resolver on every path.
    for matter_id in ("ghost-matter", "UPPERCASE", matter.matter_id):
        resp = internal_client.post(
            f"/internal/matters/{matter_id}/runs/r1/resume", headers=_NO_AUTH
        )
        assert resp.status_code == 401, matter_id
