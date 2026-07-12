"""Light endpoint smoke tests for the write-tier matter API (unit 3 scaffold).

The comprehensive endpoint matrix is unit 4's job; here we prove the scaffold wires
up: the Access guard rejects/accepts via the `get_verifier` override seam, matters
list through the registry override, and the CSRF double-submit fails closed on a
mutating route.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mootloop.errors import AccessAuthError
from mootloop.models.matter import MatterConfig
from mootloop.registry import MatterRegistry
from mootloop.web.api import create_matter_api
from mootloop.web.api.deps import get_registry, get_verifier
from mootloop.web.security import AccessPrincipal

_PRINCIPAL = AccessPrincipal(email="attorney@example.com", subject="sub-1", claims={})


class _StubVerifier:
    """Accepts the literal token ``"good"``; rejects everything else (fail-closed)."""

    def verify(self, token: str | None) -> AccessPrincipal:
        if token == "good":
            return _PRINCIPAL
        raise AccessAuthError("stub rejects token")


@pytest.fixture
def client(tmp_path: Path, matter: MatterConfig) -> TestClient:
    registry = MatterRegistry(root=tmp_path / "matters")
    registry.create(matter)
    app = create_matter_api()
    app.dependency_overrides[get_verifier] = _StubVerifier
    app.dependency_overrides[get_registry] = lambda: registry
    return TestClient(app)


def test_matters_requires_valid_access(client: TestClient) -> None:
    assert client.get("/api/matters").status_code == 401


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
