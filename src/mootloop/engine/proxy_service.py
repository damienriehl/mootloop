"""Start Squid with credentials derived from the service-user secrets store."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from mootloop import secrets
from mootloop.engine.isolation import (
    LEGAL_PROXY_PASSWORD_FILE_ENV,
    PROXY_PASSWORD_FILE_ENV,
    ProxyIdentity,
)

PASSWORD_FILE = "/tmp/mootloop-squid-passwords"


def _read_password(env_name: str) -> str:
    password_path = os.environ.get(env_name)
    if not password_path:
        raise SystemExit(f"{env_name} is not configured")
    path = Path(password_path)
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise SystemExit("egress proxy password path must be a regular file")
        password = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit("egress proxy password file is unreadable") from exc
    if not password:
        raise SystemExit("egress proxy password file is empty")
    secrets.register_secret(password)
    return password


def _password_line(identity: ProxyIdentity, password: str) -> str:
    try:
        result = subprocess.run(
            ["openssl", "passwd", "-6", "-stdin"],
            input=password,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit("could not hash egress proxy password") from exc
    password_hash = result.stdout.strip()
    parts = password_hash.split("$")
    if len(parts) != 4 or parts[:2] != ["", "6"] or not all(parts[2:]):
        raise SystemExit("could not hash egress proxy password")
    return f"{identity}:{password_hash}\n"


def main() -> None:
    model_password = _read_password(PROXY_PASSWORD_FILE_ENV)
    legal_password = _read_password(LEGAL_PROXY_PASSWORD_FILE_ENV)
    if model_password == legal_password:
        raise SystemExit("model and legal egress proxy passwords must differ")
    fd = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(_password_line(ProxyIdentity.MODEL, model_password))
        handle.write(_password_line(ProxyIdentity.LEGAL, legal_password))
    os.execvp("squid", ["squid", "-NYCd", "1"])


if __name__ == "__main__":  # pragma: no cover - container entry point
    main()
