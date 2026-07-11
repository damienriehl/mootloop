"""The ONE module that makes outbound network calls (plan D3/H9 egress control).

Every verification client goes through `fetch`, which enforces the egress policy:

- **Host allowlist** — a frozen constant; a request to any other host is refused.
- **No raw URLs** — callers build a structured `HttpRequest` via a client's builder;
  there is no code path that fetches a URL taken from ingested content (C1).
- **Timeout** on every call; a token is injected from the environment via
  `mootloop.secrets` and is never logged.

The core stays sync: the actual I/O is async (`httpx.AsyncClient`) behind an
``anyio.run`` facade (AGENTS.md pattern). Tests inject an ``httpx.MockTransport`` (or
patch with respx) so no test ever touches the network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import anyio
import httpx

from mootloop.errors import EgressError
from mootloop.secrets import load_secret

# Re-exported so client modules can catch network errors and reference the transport
# type WITHOUT importing httpx — this module is the sole httpx importer (H9 invariant).
HttpError = httpx.HTTPError
Transport = httpx.MockTransport

# Fixed egress allowlist (plan H9). Extend via config later — keep this constant + a
# comment as the single choke-point; private/link-local IPs must additionally be
# blocked post-DNS before any real-network host beyond these is added.
ALLOWED_HOSTS: frozenset[str] = frozenset(
    {
        "www.courtlistener.com",
        "api.courtlistener.com",
        "www.revisor.mn.gov",
    }
)

DEFAULT_TIMEOUT = 20.0


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


async def _afetch(
    request: HttpRequest,
    timeout: float,
    transport: httpx.MockTransport | None,
) -> HttpResponse:
    url = f"https://{request.host}{request.path}"
    async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
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
    return anyio.run(_afetch, request, timeout, transport)
