"""Hosted execution-mode and authenticated proxy configuration."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from urllib.parse import quote, urlsplit, urlunsplit

from mootloop import secrets
from mootloop.errors import EgressError

PROXY_URL_ENV = "MOOTLOOP_EGRESS_PROXY_URL"
PROXY_PASSWORD_ENV = "MOOTLOOP_EGRESS_PROXY_PASSWORD"
PROXY_PASSWORD_FILE_ENV = "MOOTLOOP_EGRESS_PROXY_PASSWORD_FILE"
PROVIDER_VAULT_ENV = "MOOTLOOP_PROVIDER_VAULT"
PROVIDER_CONFIG_DIR_ENV = "MOOTLOOP_PROVIDER_CONFIG_DIR"
CONTROL_DIR_ENV = "MOOTLOOP_CONTROL_DIR"
SECRETS_DIR_ENV = "MOOTLOOP_SECRETS_DIR"
HOSTED_PROXY_HOST = "egress-proxy"


def hosted_wrapper() -> list[str]:
    """The mandatory preflight wrapper for a hosted provider subprocess."""
    return [sys.executable, "-I", "-m", "mootloop.engine.egress_exec", "--"]


@dataclass(frozen=True)
class HostedProxy:
    url: str

    @classmethod
    def from_env(cls) -> HostedProxy:
        raw_url = os.environ.get(PROXY_URL_ENV)
        password = secrets.load_secret(PROXY_PASSWORD_ENV)
        if not raw_url:
            raise EgressError(f"hosted mode requires {PROXY_URL_ENV}")
        if not password:
            raise EgressError(f"hosted mode requires {PROXY_PASSWORD_ENV}")
        parsed = urlsplit(raw_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname != HOSTED_PROXY_HOST
            or parsed.port != 3128
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise EgressError(
                "hosted proxy must be exactly http://egress-proxy:3128 with no URL path"
            )
        if parsed.username or parsed.password:
            raise EgressError("hosted proxy credentials must come from the secrets store")
        secrets.register_secret(password)
        netloc = f"mootloop:{quote(password, safe='')}@{HOSTED_PROXY_HOST}:3128"
        return cls(url=urlunsplit(("http", netloc, "", "", "")))
