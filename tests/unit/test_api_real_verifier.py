"""Real-cryptography end-to-end for the write-tier matter API (phase FE-0, unit 4).

Unlike ``test_api_endpoints.py`` (which stubs the verifier via ``dependency_overrides``),
this drives a route through a GENUINE `CfAccessVerifier`: an RSA keypair, a real JWKS,
RS256 signing, and the app's own ``get_verifier`` -> ``CfAccessVerifier.from_env`` seam.
Only the public JWKS *network read* is redirected to the locally built key set — the
signature/claim checks are the real path wired into `create_matter_api`. It proves the
app admits a cryptographically valid Cf-Access token and rejects a bad one.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from mootloop.models.matter import MatterConfig
from mootloop.registry import MatterRegistry
from mootloop.web import security
from mootloop.web.api import create_matter_api
from mootloop.web.api.deps import get_registry

TEAM = "acme"
ISSUER = "https://acme.cloudflareaccess.com"
AUD = "real-aud-tag"
EMAIL = "attorney@example.com"
KID = "e2e-key-1"


@pytest.fixture(scope="module")
def keypair() -> tuple[bytes, dict[str, Any]]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    jwk = json.loads(RSAAlgorithm.to_jwk(key.public_key()))
    jwk["kid"] = KID
    return priv_pem, {"keys": [jwk]}


def _mint(priv_pem: bytes, *, email: str = EMAIL, exp_delta: float = 600.0) -> str:
    payload = {
        "aud": AUD,
        "iss": ISSUER,
        "exp": int(time.time() + exp_delta),
        "sub": "user-e2e",
        "email": email,
    }
    return jwt.encode(payload, priv_pem, algorithm="RS256", headers={"kid": KID})


@pytest.fixture
def client(
    tmp_path: Path,
    matter: MatterConfig,
    keypair: tuple[bytes, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> TestClient:
    _, jwks = keypair
    # Real Access config: get_verifier -> CfAccessVerifier.from_env() builds a real,
    # crypto-verifying verifier. Redirect ONLY the JWKS network read to the local keys.
    monkeypatch.setenv("CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CF_ACCESS_AUD", AUD)
    monkeypatch.setenv("CF_ACCESS_ALLOWED_EMAIL", EMAIL)
    monkeypatch.setattr(security, "_default_jwks_fetcher", lambda _url: (lambda: jwks))

    registry = MatterRegistry(root=tmp_path / "matters")
    registry.create(matter)
    app = create_matter_api()
    # The verifier is the REAL one (not overridden); only the registry is injected.
    app.dependency_overrides[get_registry] = lambda: registry
    return TestClient(app)


def test_real_verifier_admits_valid_token(
    client: TestClient, keypair: tuple[bytes, dict[str, Any]], matter: MatterConfig
) -> None:
    priv, _ = keypair
    resp = client.get("/api/matters", headers={"cf-access-jwt-assertion": _mint(priv)})
    assert resp.status_code == 200
    assert [m["matter_id"] for m in resp.json()] == [matter.matter_id]


def test_real_verifier_rejects_wrong_email(
    client: TestClient, keypair: tuple[bytes, dict[str, Any]]
) -> None:
    priv, _ = keypair
    token = _mint(priv, email="intruder@evil.example")
    resp = client.get("/api/matters", headers={"cf-access-jwt-assertion": token})
    assert resp.status_code == 401


def test_real_verifier_rejects_expired_token(
    client: TestClient, keypair: tuple[bytes, dict[str, Any]]
) -> None:
    priv, _ = keypair
    token = _mint(priv, exp_delta=-30.0)
    resp = client.get("/api/matters", headers={"cf-access-jwt-assertion": token})
    assert resp.status_code == 401
