"""Hosted-tier perimeter primitives (plan FD-1/FD-2/FD-3, phase FE-0).

Three fail-closed controls, none of which the demo tier (`web/app.py`) uses:

- `CfAccessVerifier` — verifies the `Cf-Access-Jwt-Assertion` header Cloudflare Access
  stamps on every request: RS256 asserted by us (never read from the token header),
  signature checked against the cached team JWKS, and `aud`/`iss`/`exp`/`email` pinned.
  A JWKS fetch failure rejects — the verify path never falls through to "unverified".
- `InternalAuth` — constant-time check of the driver/BFF internal secret, replacing
  the dead localhost trust on the shared Docker network.
- `RateLimitMiddleware` — a pure-ASGI token-bucket limiter on write methods (a
  Starlette `BaseHTTPMiddleware` would break SSE — first-party lesson).

All three take injectable clocks / dependencies so the verify logic is testable fully
offline. The network fetch is a bounded, injectable synchronous facade; only the ASGI
middleware `__call__` is async, because ASGI requires it.
"""

from __future__ import annotations

import hmac
import json
import math
import os
import time
import urllib.request
from collections.abc import Awaitable, Callable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jwt
from jwt.algorithms import RSAAlgorithm

from mootloop.errors import AccessAuthError, InternalAuthError
from mootloop.secrets import SECRETS_FILE, load_secret

# --- Cloudflare Access JWT verification (FD-2) --------------------------------

_CERTS_PATH = "/cdn-cgi/access/certs"
_DEFAULT_JWKS_TTL = 600.0
_JWKS_TIMEOUT = 5.0

JwksFetcher = Callable[[], Mapping[str, Any]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class AccessPrincipal:
    """The verified caller. `email` is the pinned identity; `claims` is the raw set."""

    email: str
    subject: str
    claims: Mapping[str, Any]


def _team_base_url(team_domain: str) -> str:
    """Normalize a team slug (``acme``) or full domain into the team base URL."""
    domain = team_domain.strip().rstrip("/")
    if not domain:
        raise AccessAuthError("CF_ACCESS_TEAM_DOMAIN is empty")
    if domain.startswith(("http://", "https://")):
        return domain
    if "." not in domain:
        domain = f"{domain}.cloudflareaccess.com"
    return f"https://{domain}"


def _default_jwks_fetcher(certs_url: str) -> JwksFetcher:
    # Stdlib fetch on purpose: httpx is egress-contained to citations/http.py (an
    # invariant). This is a bounded control-plane read of the public Access JWKS, not
    # persona/citation egress. Any failure propagates and the verifier fails closed.
    def fetch() -> Mapping[str, Any]:
        with urllib.request.urlopen(certs_url, timeout=_JWKS_TIMEOUT) as resp:
            data: Any = json.loads(resp.read())
        if not isinstance(data, dict):
            raise AccessAuthError("JWKS response was not an object")
        return data

    return fetch


class CfAccessVerifier:
    """Verify a Cloudflare Access application token, fail-closed on any error.

    The algorithm is asserted here (``algorithms=["RS256"]``) — the token header's
    ``alg`` is never trusted, so ``alg=none`` and HS256 forgeries are rejected. ``aud``,
    ``iss``, and ``exp`` are pinned; the ``email`` claim must equal the configured
    allowed email (compared case-insensitively — email local-parts are effectively
    case-insensitive in practice and Access normalizes casing). A service token carries
    no ``email`` claim, so it is rejected on this (matter) surface.

    Dependencies are injectable for offline tests: ``jwks_fetcher`` returns the JWKS
    mapping (tests pass a locally built one, or one that raises/empties to prove
    fail-closed), and ``now`` supplies epoch seconds for the ``exp`` check and the
    bounded JWKS cache TTL.
    """

    def __init__(
        self,
        *,
        team_domain: str,
        aud: str,
        allowed_email: str,
        jwks_fetcher: JwksFetcher | None = None,
        now: Clock = time.time,
        jwks_ttl: float = _DEFAULT_JWKS_TTL,
        leeway: float = 0.0,
    ) -> None:
        if not aud:
            raise AccessAuthError("CF_ACCESS_AUD is empty")
        if not allowed_email:
            raise AccessAuthError("CF_ACCESS_ALLOWED_EMAIL is empty")
        self.base_url = _team_base_url(team_domain)
        self.issuer = self.base_url
        self.aud = aud
        self.allowed_email = allowed_email.strip().lower()
        self.certs_url = self.base_url + _CERTS_PATH
        self._fetch: JwksFetcher = jwks_fetcher or _default_jwks_fetcher(self.certs_url)
        self._now = now
        self._jwks_ttl = jwks_ttl
        self._leeway = leeway
        self._jwks_cache: Mapping[str, Any] | None = None
        self._jwks_fetched_at = 0.0

    @classmethod
    def from_env(cls, *, now: Clock = time.time) -> CfAccessVerifier:
        """Build from ``CF_ACCESS_TEAM_DOMAIN`` / ``CF_ACCESS_AUD`` /
        ``CF_ACCESS_ALLOWED_EMAIL``. Missing/empty config fails closed."""
        team = os.environ.get("CF_ACCESS_TEAM_DOMAIN", "")
        aud = os.environ.get("CF_ACCESS_AUD", "")
        email = os.environ.get("CF_ACCESS_ALLOWED_EMAIL", "")
        if not (team and aud and email):
            raise AccessAuthError("CF_ACCESS_TEAM_DOMAIN/AUD/ALLOWED_EMAIL not configured")
        return cls(team_domain=team, aud=aud, allowed_email=email, now=now)

    def _get_jwks(self) -> Mapping[str, Any]:
        now = self._now()
        cached = self._jwks_cache
        if cached is not None and (now - self._jwks_fetched_at) < self._jwks_ttl:
            return cached
        try:
            jwks = self._fetch()
        except AccessAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 — any fetch/parse failure fails closed
            raise AccessAuthError("JWKS fetch failed") from exc
        keys = jwks.get("keys") if isinstance(jwks, Mapping) else None
        if not keys:
            raise AccessAuthError("JWKS contained no keys")
        self._jwks_cache = jwks
        self._jwks_fetched_at = now
        return jwks

    def _public_key(self, token: str) -> Any:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise AccessAuthError("malformed token header") from exc
        kid = header.get("kid")
        jwks = self._get_jwks()
        for candidate in jwks.get("keys", ()):
            if candidate.get("kid") == kid:
                try:
                    return RSAAlgorithm.from_jwk(json.dumps(candidate))
                except (ValueError, TypeError, jwt.InvalidKeyError) as exc:
                    raise AccessAuthError("unusable JWK for token kid") from exc
        raise AccessAuthError("no JWKS key matches the token kid")

    def verify(self, token: str | None) -> AccessPrincipal:
        """Return the verified principal, or raise `AccessAuthError` on any failure."""
        if not token:
            raise AccessAuthError("missing Cf-Access-Jwt-Assertion")
        public_key = self._public_key(token)
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=public_key,
                algorithms=["RS256"],
                audience=self.aud,
                issuer=self.issuer,
                leeway=self._leeway,
                options={
                    "require": ["exp", "iss", "aud"],
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_signature": True,
                    "verify_exp": False,
                },
            )
        except jwt.InvalidTokenError as exc:
            raise AccessAuthError(f"token rejected: {type(exc).__name__}") from exc
        self._assert_not_expired(claims)
        email = claims.get("email")
        if not isinstance(email, str) or email.strip().lower() != self.allowed_email:
            raise AccessAuthError("email claim absent or not the allowed identity")
        return AccessPrincipal(
            email=email,
            subject=str(claims.get("sub", "")),
            claims=claims,
        )

    def _assert_not_expired(self, claims: Mapping[str, Any]) -> None:
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)):
            raise AccessAuthError("exp claim missing or non-numeric")
        if self._now() > float(exp) + self._leeway:
            raise AccessAuthError("token expired")


# --- Internal driver-secret check (FD-1) --------------------------------------


class InternalAuth:
    """Constant-time check of the internal driver/BFF secret.

    The presented ``X-Mootloop-Internal`` header value is compared with
    ``hmac.compare_digest`` against ``MOOTLOOP_INTERNAL_SECRET`` (resolved through the
    shared secrets loader — env parsing is not re-implemented). A missing/empty
    configured secret rejects (fail closed); localhost trust is dead on a shared
    Docker network.
    """

    HEADER = "x-mootloop-internal"
    ENV_KEY = "MOOTLOOP_INTERNAL_SECRET"

    def __init__(self, *, secret: str | None = None, secrets_file: Path = SECRETS_FILE) -> None:
        self._secret = secret
        self._secrets_file = secrets_file

    @classmethod
    def from_env(cls, *, secrets_file: Path = SECRETS_FILE) -> InternalAuth:
        return cls(secrets_file=secrets_file)

    def _expected(self) -> str | None:
        if self._secret is not None:
            return self._secret
        return load_secret(self.ENV_KEY, secrets_file=self._secrets_file)

    def verify(self, presented: str | None) -> None:
        """Raise `InternalAuthError` unless ``presented`` matches the configured secret."""
        expected = self._expected()
        if not expected:
            raise InternalAuthError("internal secret not configured")
        if not presented or not hmac.compare_digest(presented, expected):
            raise InternalAuthError("internal secret mismatch")


# --- Pure-ASGI rate-limit middleware ------------------------------------------

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_DEFAULT_CAPACITY = 20.0
_DEFAULT_REFILL_PER_SEC = 2.0


@dataclass
class _Bucket:
    tokens: float
    updated_at: float


@dataclass
class RateLimitMiddleware:
    """Pure-ASGI token-bucket limiter applied to write methods.

    One bucket per client key. The key is the first present of the
    ``cf-connecting-ip`` header, the first ``x-forwarded-for`` hop, then the ASGI
    ``client`` host — deterministic, and behind Cloudflare/Traefik the edge headers
    identify the real caller. Non-write methods and non-HTTP scopes pass straight
    through. On exhaustion a 429 with ``Retry-After`` is sent and the inner app is
    never called. State is per-process in-memory (sufficient for the single-box tier).
    ``now`` (monotonic seconds) is injectable for deterministic tests.
    """

    app: ASGIApp
    capacity: float = _DEFAULT_CAPACITY
    refill_per_sec: float = _DEFAULT_REFILL_PER_SEC
    now: Clock = time.monotonic
    methods: frozenset[str] = _WRITE_METHODS
    _buckets: dict[str, _Bucket] = field(default_factory=dict, init=False, repr=False)

    @classmethod
    def from_env(cls, app: ASGIApp, *, now: Clock = time.monotonic) -> RateLimitMiddleware:
        capacity = float(os.environ.get("MOOTLOOP_RATE_CAPACITY", _DEFAULT_CAPACITY))
        refill = float(os.environ.get("MOOTLOOP_RATE_REFILL_PER_SEC", _DEFAULT_REFILL_PER_SEC))
        return cls(app=app, capacity=capacity, refill_per_sec=refill, now=now)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or scope.get("method") not in self.methods:
            await self.app(scope, receive, send)
            return
        retry_after = self._check(self._client_key(scope))
        if retry_after is None:
            await self.app(scope, receive, send)
            return
        await self._send_429(send, retry_after)

    def _check(self, key: str) -> int | None:
        """Consume a token; return ``None`` if allowed, else the Retry-After seconds."""
        now = self.now()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(tokens=self.capacity, updated_at=now)
            self._buckets[key] = bucket
        elapsed = max(0.0, now - bucket.updated_at)
        bucket.tokens = min(self.capacity, bucket.tokens + elapsed * self.refill_per_sec)
        bucket.updated_at = now
        if bucket.tokens >= 1.0:
            bucket.tokens -= 1.0
            return None
        deficit = 1.0 - bucket.tokens
        wait = math.inf if self.refill_per_sec <= 0 else deficit / self.refill_per_sec
        return max(1, math.ceil(wait)) if wait != math.inf else 3600

    @staticmethod
    def _client_key(scope: Scope) -> str:
        headers: dict[bytes, bytes] = dict(scope.get("headers") or [])
        for name in (b"cf-connecting-ip", b"x-forwarded-for"):
            raw = headers.get(name)
            if raw:
                return raw.decode("latin-1").split(",")[0].strip()
        client = scope.get("client")
        if client:
            return str(client[0])
        return "anonymous"

    @staticmethod
    async def _send_429(send: Send, retry_after: int) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"text/plain; charset=utf-8"),
                    (b"retry-after", str(retry_after).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": b"rate limit exceeded"})
