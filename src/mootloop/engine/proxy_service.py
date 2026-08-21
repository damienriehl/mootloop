"""Start Squid with credentials derived from the service-user secrets store."""

from __future__ import annotations

import base64
import hashlib
import os
import stat
from pathlib import Path

from mootloop import secrets
from mootloop.engine.isolation import PROXY_PASSWORD_FILE_ENV

PASSWORD_FILE = "/tmp/mootloop-squid-passwords"


def main() -> None:
    password_path = os.environ.get(PROXY_PASSWORD_FILE_ENV)
    if not password_path:
        raise SystemExit(f"{PROXY_PASSWORD_FILE_ENV} is not configured")
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
    digest = base64.b64encode(hashlib.sha1(password.encode("utf-8")).digest()).decode("ascii")
    fd = os.open(PASSWORD_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"mootloop:{{SHA}}{digest}\n")
    os.execvp("squid", ["squid", "-NYCd", "1"])


if __name__ == "__main__":  # pragma: no cover - container entry point
    main()
