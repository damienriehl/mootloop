"""Prove the built driver image generates credentials Squid can verify."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from mootloop.engine.isolation import ProxyIdentity
from mootloop.engine.proxy_service import _password_line

MODEL_PASSWORD = "synthetic-model-password"
LEGAL_PASSWORD = "synthetic-legal-password"
HELPER = "/usr/lib/squid/basic_ncsa_auth"


def _assert_auth(path: Path, identity: ProxyIdentity, password: str, expected: str) -> None:
    result = subprocess.run(
        [HELPER, str(path)],
        input=f"{identity} {password}\n",
        check=True,
        capture_output=True,
        text=True,
    )
    if not result.stdout.startswith(expected):
        raise SystemExit(f"Squid auth helper returned {result.stdout.split(maxsplit=1)[0]!r}")


def main() -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8") as handle:
        handle.write(_password_line(ProxyIdentity.MODEL, MODEL_PASSWORD))
        handle.write(_password_line(ProxyIdentity.LEGAL, LEGAL_PASSWORD))
        handle.flush()
        path = Path(handle.name)
        _assert_auth(path, ProxyIdentity.MODEL, MODEL_PASSWORD, "OK")
        _assert_auth(path, ProxyIdentity.LEGAL, LEGAL_PASSWORD, "OK")
        _assert_auth(path, ProxyIdentity.MODEL, LEGAL_PASSWORD, "ERR")
        _assert_auth(path, ProxyIdentity.LEGAL, MODEL_PASSWORD, "ERR")
    print("Squid auth helper smoke passed")


if __name__ == "__main__":
    main()
