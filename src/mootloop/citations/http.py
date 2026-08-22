"""The ONE module that makes public-legal-source network calls (plan D3/H9).

Every verification client goes through `fetch`, which enforces the egress policy:

- **Host and route allowlist** — every method/path pair is fixed in code.
- **No raw URLs** — callers build a structured `HttpRequest` via a client's builder;
  there is no code path that fetches a URL taken from ingested content (C1).
- **Hosted proxy** — hosted calls use the authenticated fixed proxy explicitly and
  ignore ambient proxy variables.
- **Outbound privacy** — canaries, denylisted values, and exact secrets block before
  request serialization or transport.
- **Timeout** on every call; a token is injected from the environment via
  `mootloop.secrets` and is never logged.

The core stays sync: the actual I/O is async (`httpx.AsyncClient`) behind an
``anyio.run`` facade (AGENTS.md pattern). Tests inject an ``httpx.MockTransport`` (or
patch with respx) so no test ever touches the network.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

import anyio
import httpx

from mootloop.errors import EgressError, OutboundPrivacyError
from mootloop.privacy import serialize_outbound
from mootloop.runtime import RUNTIME_MODE_ENV, RuntimeMode
from mootloop.secrets import load_secret

# Re-exported so client modules can catch network errors and reference the transport
# type WITHOUT importing httpx — this module is the sole httpx importer (H9 invariant).
HttpError = httpx.HTTPError
Transport = httpx.MockTransport

# Fixed egress allowlist (plan H9). Extend via config later — keep this constant + a
# comment as the single choke-point; private/link-local IPs must additionally be
# blocked post-DNS before any real-network host beyond these is added.
_COURTLISTENER_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"/api/rest/v4/citation-lookup/")),
    ("GET", re.compile(r"/api/rest/v4/search/")),
    ("GET", re.compile(r"/api/rest/v4/clusters/[1-9][0-9]*/")),
    ("GET", re.compile(r"/api/rest/v4/opinions/[1-9][0-9]*/")),
)
_ALLOWED_ROUTES: dict[str, tuple[tuple[str, re.Pattern[str]], ...]] = {
    "www.courtlistener.com": _COURTLISTENER_ROUTES,
    "api.courtlistener.com": _COURTLISTENER_ROUTES,
    "www.revisor.mn.gov": (
        ("GET", re.compile(r"/statutes/cite/[0-9][0-9A-Za-z.\-]*")),
        ("GET", re.compile(r"/court_rules/(?:cp|gp)/id/[1-9][0-9]*/")),
    ),
}
ALLOWED_HOSTS: frozenset[str] = frozenset(_ALLOWED_ROUTES)

DEFAULT_TIMEOUT = 20.0
COURTLISTENER_TOKEN_ENV = "COURTLISTENER_TOKEN"


@dataclass(frozen=True)
class HttpRequest:
    """A structured request built by one of our clients — never a raw caller URL.

    ``host`` must be in `ALLOWED_HOSTS`; the URL is assembled here from ``host`` +
    ``path`` (+ ``params``), so ingested content can never become a fetch target.
    """

    method: Literal["GET", "POST"]
    host: str
    path: str
    params: dict[str, str] | None = None
    json_body: dict[str, Any] | None = None
    auth_token_env: str | None = None


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    text: str
    json_body: Any | None


def _headers(request: HttpRequest) -> dict[str, str]:
    headers = {"User-Agent": "mootloop/0.0 (+https://github.com/) citation-verifier"}
    if request.auth_token_env:
        token = load_secret(request.auth_token_env)
        if token:
            headers["Authorization"] = f"Token {token}"
    return headers


def _validate(request: HttpRequest) -> None:
    if request.host not in ALLOWED_HOSTS:
        raise EgressError(
            f"host {request.host!r} is not in the egress allowlist {sorted(ALLOWED_HOSTS)}"
        )
    if not request.path.startswith("/"):
        raise EgressError(f"path {request.path!r} must be an absolute path built by a client")
    rules = _ALLOWED_ROUTES[request.host]
    if not any(
        method == request.method and pattern.fullmatch(request.path)
        for method, pattern in rules
    ):
        raise EgressError(
            f"route {request.method} https://{request.host}{request.path} is not approved"
        )
    if request.auth_token_env not in (None, COURTLISTENER_TOKEN_ENV):
        raise EgressError(
            f"secret name {request.auth_token_env!r} is not approved for legal egress"
        )
    if request.auth_token_env and request.host not in {
        "www.courtlistener.com",
        "api.courtlistener.com",
    }:
        raise EgressError("CourtListener credentials may be sent only to CourtListener")


def _hosted_proxy_url() -> str | None:
    if os.environ.get(RUNTIME_MODE_ENV) != RuntimeMode.HOSTED:
        return None
    from mootloop.engine.isolation import HostedProxy, ProxyIdentity

    return HostedProxy.from_env(ProxyIdentity.LEGAL).url


def _privacy_preflight(request: HttpRequest) -> None:
    payload = {
        "method": request.method,
        "host": request.host,
        "path": request.path,
        "params": request.params,
        "json_body": request.json_body,
    }
    scrubbed = json.loads(serialize_outbound(payload))
    if scrubbed != payload:
        raise OutboundPrivacyError("outbound legal request required secret redaction")


async def _afetch(
    request: HttpRequest,
    timeout: float,
    transport: httpx.MockTransport | None,
) -> HttpResponse:
    url = f"https://{request.host}{request.path}"
    proxy = _hosted_proxy_url()
    async with httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        proxy=proxy,
        trust_env=proxy is None,
        follow_redirects=False,
    ) as client:
        response = await client.request(
            request.method,
            url,
            params=request.params,
            json=request.json_body,
            headers=_headers(request),
        )
    body: Any | None
    try:
        body = response.json()
    except ValueError:
        body = None
    return HttpResponse(status_code=response.status_code, text=response.text, json_body=body)


def fetch(
    request: HttpRequest,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    transport: httpx.MockTransport | None = None,
) -> HttpResponse:
    """Perform one policy-checked HTTP request (sync facade over async httpx).

    Raises `EgressError` if the host is off-allowlist or the path is not builder-made.
    Network/timeout errors propagate as ``httpx.HTTPError`` — clients catch them and
    fail closed (a failed fetch becomes a ``pending`` verification, never ``verified``).
    """
    _validate(request)
    _privacy_preflight(request)
    return anyio.run(_afetch, request, timeout, transport)
