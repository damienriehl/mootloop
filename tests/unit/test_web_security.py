"""FE-0 perimeter: Cloudflare Access JWT verify, internal-auth, ASGI rate limit,
and the FD-3 redaction analogues. Every path is exercised offline."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from mootloop.errors import AccessAuthError, InternalAuthError
from mootloop.secrets import redact, register_secret
from mootloop.web.security import (
    CfAccessVerifier,
    InternalAuth,
    RateLimitMiddleware,
)

TEAM = "acme"
ISSUER = "https://acme.cloudflareaccess.com"
AUD = "aud-tag-123"
EMAIL = "attorney@example.com"
NOW = 1_000_000.0
KID = "test-key-1"


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


_UNSET: Any = object()


def _mint(
    priv_pem: bytes,
    *,
    algorithm: str = "RS256",
    key: Any = _UNSET,
    aud: str = AUD,
    iss: str = ISSUER,
    email: str | None = EMAIL,
    exp: float = NOW + 600,
    kid: str = KID,
) -> str:
    payload: dict[str, Any] = {"aud": aud, "iss": iss, "exp": int(exp), "sub": "user-1"}
    if email is not None:
        payload["email"] = email
    if key is not _UNSET:
        signing_key = key
    elif algorithm == "none":
        signing_key = ""
    else:
        signing_key = priv_pem
    return jwt.encode(payload, signing_key, algorithm=algorithm, headers={"kid": kid})


def _verifier(jwks: Mapping[str, Any], **over: Any) -> CfAccessVerifier:
    kw: dict[str, Any] = {
        "team_domain": TEAM,
        "aud": AUD,
        "allowed_email": EMAIL,
        "jwks_fetcher": lambda: jwks,
        "now": lambda: NOW,
    }
    kw.update(over)
    return CfAccessVerifier(**kw)


# --- CfAccessVerifier matrix --------------------------------------------------


def test_valid_token_passes(keypair) -> None:
    priv, jwks = keypair
    principal = _verifier(jwks).verify(_mint(priv))
    assert principal.email == EMAIL
    assert principal.subject == "user-1"


def test_wrong_aud_rejects(keypair) -> None:
    priv, jwks = keypair
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(_mint(priv, aud="other-app"))


def test_wrong_iss_rejects(keypair) -> None:
    priv, jwks = keypair
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(_mint(priv, iss="https://evil.cloudflareaccess.com"))


def test_wrong_email_rejects(keypair) -> None:
    priv, jwks = keypair
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(_mint(priv, email="attacker@evil.com"))


def test_email_compare_is_case_insensitive(keypair) -> None:
    priv, jwks = keypair
    principal = _verifier(jwks).verify(_mint(priv, email="Attorney@Example.com"))
    assert principal.email == "Attorney@Example.com"


def test_missing_email_claim_rejects(keypair) -> None:
    # A Cloudflare service token carries no email claim -> barred from matter routes.
    priv, jwks = keypair
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(_mint(priv, email=None))


def test_hs256_forgery_rejects(keypair) -> None:
    priv, jwks = keypair
    forged = _mint(priv, algorithm="HS256", key="shared-secret-" + "x" * 32)
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(forged)


def test_alg_none_rejects(keypair) -> None:
    priv, jwks = keypair
    none_token = _mint(priv, algorithm="none")
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(none_token)


def test_expired_token_rejects(keypair) -> None:
    priv, jwks = keypair
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(_mint(priv, exp=NOW - 1))


def test_unfetchable_jwks_rejects_fail_closed(keypair) -> None:
    priv, _ = keypair

    def boom() -> Mapping[str, Any]:
        raise ConnectionError("edge unreachable")

    verifier = _verifier({"keys": []}, jwks_fetcher=boom)
    with pytest.raises(AccessAuthError):
        verifier.verify(_mint(priv))


def test_empty_jwks_rejects_fail_closed(keypair) -> None:
    priv, _ = keypair
    verifier = _verifier({"keys": []})
    with pytest.raises(AccessAuthError):
        verifier.verify(_mint(priv))


def test_missing_token_rejects(keypair) -> None:
    _, jwks = keypair
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(None)


def test_unknown_kid_rejects(keypair) -> None:
    priv, jwks = keypair
    with pytest.raises(AccessAuthError):
        _verifier(jwks).verify(_mint(priv, kid="rotated-away"))


def test_from_env_requires_config(monkeypatch) -> None:
    for var in ("CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "CF_ACCESS_ALLOWED_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(AccessAuthError):
        CfAccessVerifier.from_env()


# --- InternalAuth -------------------------------------------------------------


def test_internal_auth_correct_secret_passes() -> None:
    InternalAuth(secret="topsecret").verify("topsecret")  # no raise


def test_internal_auth_wrong_secret_rejects() -> None:
    with pytest.raises(InternalAuthError):
        InternalAuth(secret="topsecret").verify("guess")


def test_internal_auth_missing_configured_secret_rejects() -> None:
    with pytest.raises(InternalAuthError):
        InternalAuth(secret="").verify("anything")


def test_internal_auth_missing_presented_rejects() -> None:
    with pytest.raises(InternalAuthError):
        InternalAuth(secret="topsecret").verify(None)


def test_internal_auth_loads_via_secrets_loader(tmp_path) -> None:
    secrets = tmp_path / "secrets.env"
    secrets.write_text("MOOTLOOP_INTERNAL_SECRET=fromfile\n", encoding="utf-8")
    auth = InternalAuth(secrets_file=secrets)
    auth.verify("fromfile")
    with pytest.raises(InternalAuthError):
        auth.verify("fromfile-wrong")


# --- Rate limiter (driven as an ASGI app) -------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


async def _inner(scope: Any, receive: Any, send: Any) -> None:
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _scope(method: str = "POST", host: str = "1.2.3.4") -> dict[str, Any]:
    return {"type": "http", "method": method, "headers": [], "client": (host, 5000)}


def _drive(mw: RateLimitMiddleware, scope: dict[str, Any]) -> list[dict[str, Any]]:
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    asyncio.run(mw(scope, receive, send))
    return sent


def _status(sent: list[dict[str, Any]]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def test_rate_limit_under_limit_passes() -> None:
    mw = RateLimitMiddleware(_inner, capacity=2, refill_per_sec=0.0, now=_Clock())
    assert _status(_drive(mw, _scope())) == 200
    assert _status(_drive(mw, _scope())) == 200


def test_rate_limit_over_limit_returns_429_with_retry_after() -> None:
    clock = _Clock()
    mw = RateLimitMiddleware(_inner, capacity=1, refill_per_sec=1.0, now=clock)
    assert _status(_drive(mw, _scope())) == 200
    sent = _drive(mw, _scope())
    assert _status(sent) == 429
    headers = dict(next(m for m in sent if m["type"] == "http.response.start")["headers"])
    assert b"retry-after" in headers


def test_rate_limit_refills_after_clock_advance() -> None:
    clock = _Clock()
    mw = RateLimitMiddleware(_inner, capacity=1, refill_per_sec=1.0, now=clock)
    assert _status(_drive(mw, _scope())) == 200
    assert _status(_drive(mw, _scope())) == 429
    clock.t = 1.0  # one token refilled
    assert _status(_drive(mw, _scope())) == 200


def test_rate_limit_ignores_read_methods() -> None:
    mw = RateLimitMiddleware(_inner, capacity=1, refill_per_sec=0.0, now=_Clock())
    for _ in range(5):
        assert _status(_drive(mw, _scope(method="GET"))) == 200


def test_rate_limit_is_per_client() -> None:
    mw = RateLimitMiddleware(_inner, capacity=1, refill_per_sec=0.0, now=_Clock())
    assert _status(_drive(mw, _scope(host="1.1.1.1"))) == 200
    assert _status(_drive(mw, _scope(host="1.1.1.1"))) == 429
    assert _status(_drive(mw, _scope(host="2.2.2.2"))) == 200


# --- FD-3 redaction analogues -------------------------------------------------


def test_redact_google_refresh_token() -> None:
    token = "1//0gAbCd_ef-GHijkLMnop1234567890"
    scrubbed = redact(f"refresh={token}")
    assert token not in scrubbed
    assert "REDACTED" in scrubbed


def test_redact_claude_oauth_token() -> None:
    token = "sk-ant-oat01-AbC_def-GHijkLMno123456789"
    assert token not in redact(f"CLAUDE_CODE_OAUTH_TOKEN={token}")


def test_redact_registered_exact_value() -> None:
    register_secret("ntfy-topic-9f83aa-not-shaped")
    scrubbed = redact("push to ntfy-topic-9f83aa-not-shaped now")
    assert "ntfy-topic-9f83aa-not-shaped" not in scrubbed
    assert "REDACTED" in scrubbed


def test_redact_existing_patterns_still_work() -> None:
    token = "b" * 40
    scrubbed = redact(f"Authorization: Bearer {token} plus sk-abcdef012345")
    assert token not in scrubbed
    assert "sk-abcdef012345" not in scrubbed
