"""Preflight and Landlock-confine the hosted model process."""

from __future__ import annotations

import ctypes
import errno
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from mootloop.engine.isolation import (
    CONTROL_DIR_ENV,
    HOSTED_PROXY_HOST,
    PROVIDER_CONFIG_DIR_ENV,
    PROVIDER_VAULT_ENV,
    SECRETS_DIR_ENV,
    ProxyIdentity,
)
from mootloop.privacy import CANARY_REGISTRY_ENV

_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

_EXECUTE = 1 << 0
_WRITE_FILE = 1 << 1
_READ_FILE = 1 << 2
_READ_DIR = 1 << 3
_REMOVE_DIR = 1 << 4
_REMOVE_FILE = 1 << 5
_MAKE_CHAR = 1 << 6
_MAKE_DIR = 1 << 7
_MAKE_REG = 1 << 8
_MAKE_SOCK = 1 << 9
_MAKE_FIFO = 1 << 10
_MAKE_BLOCK = 1 << 11
_MAKE_SYM = 1 << 12
_REFER = 1 << 13
_TRUNCATE = 1 << 14
_IOCTL_DEV = 1 << 15

_READ_EXEC = _EXECUTE | _READ_FILE | _READ_DIR
_WRITE_TREE = (
    _READ_EXEC
    | _WRITE_FILE
    | _REMOVE_DIR
    | _REMOVE_FILE
    | _MAKE_CHAR
    | _MAKE_DIR
    | _MAKE_REG
    | _MAKE_SOCK
    | _MAKE_FIFO
    | _MAKE_BLOCK
    | _MAKE_SYM
    | _REFER
    | _TRUNCATE
    | _IOCTL_DEV
)


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
    ]


def _validated_paths(env: dict[str, str]) -> tuple[Path, Path, Path, Path, Path]:
    https_proxy = env.get("HTTPS_PROXY")
    http_proxy = env.get("HTTP_PROXY")
    parsed = urlsplit(https_proxy or "")
    if (
        not https_proxy
        or https_proxy != http_proxy
        or parsed.scheme != "http"
        or parsed.hostname != HOSTED_PROXY_HOST
        or parsed.port != 3128
        or parsed.username != ProxyIdentity.MODEL
        or not parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise SystemExit("hosted egress proxy is missing or unauthenticated")
    raw_paths = (
        env.get(PROVIDER_VAULT_ENV),
        env.get(PROVIDER_CONFIG_DIR_ENV),
        env.get(CONTROL_DIR_ENV),
        env.get(SECRETS_DIR_ENV),
        env.get(CANARY_REGISTRY_ENV),
    )
    if not all(path and os.path.isabs(path) for path in raw_paths):
        raise SystemExit("hosted provider isolation paths are missing")
    vault, config, control, secrets, canary = raw_paths
    assert vault and config and control and secrets and canary
    return Path(vault), Path(config), Path(control), Path(secrets), Path(canary)


def validate_isolated_command(command: list[str], env: dict[str, str]) -> list[str]:
    """Validate the proxy and filesystem boundary before applying Landlock."""
    if not command:
        raise SystemExit("egress wrapper requires a command")
    _validated_paths(env)
    return command


def _syscall(number: int, *args: object) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    result = int(libc.syscall(number, *args))
    if result < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return result


def _handled_access(abi: int) -> int:
    access = _WRITE_TREE
    if abi < 5:
        access &= ~_IOCTL_DEV
    if abi < 3:
        access &= ~_TRUNCATE
    if abi < 2:
        access &= ~_REFER
    return access


def _allow_path(ruleset_fd: int, path: Path, access: int) -> None:
    if not path.exists():
        return
    parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        rule = _PathBeneathAttr(allowed_access=access, parent_fd=parent_fd)
        _syscall(
            _LANDLOCK_ADD_RULE,
            ruleset_fd,
            _LANDLOCK_RULE_PATH_BENEATH,
            ctypes.byref(rule),
            0,
        )
    finally:
        os.close(parent_fd)


def apply_landlock(env: dict[str, str]) -> None:
    """Allow only runtime files plus the per-run config tree; fail closed."""
    _vault, config, _control, _secrets, _canary = _validated_paths(env)
    try:
        abi = _syscall(
            _LANDLOCK_CREATE_RULESET,
            0,
            0,
            _LANDLOCK_CREATE_RULESET_VERSION,
        )
    except OSError as exc:
        raise SystemExit("hosted filesystem isolation requires Landlock") from exc
    handled = _handled_access(abi)
    ruleset = _RulesetAttr(handled_access_fs=handled)
    ruleset_fd: int | None = None
    try:
        ruleset_fd = _syscall(
            _LANDLOCK_CREATE_RULESET,
            ctypes.byref(ruleset),
            ctypes.sizeof(ruleset),
            0,
        )
        for path in (Path("/app"), Path("/usr"), Path("/lib"), Path("/lib64"), Path("/etc")):
            _allow_path(ruleset_fd, path, _READ_EXEC & handled)
        device_access = (_READ_FILE | _WRITE_FILE | _READ_DIR) & handled
        _allow_path(ruleset_fd, Path("/dev"), device_access)
        # Claude Code's native binary reads its own memory map during startup and
        # aborts with SIGABRT when Landlock hides it. Bind the rule to this process's
        # maps inode before exec; do not expose procfs generally or any other PID.
        _allow_path(ruleset_fd, Path("/proc/self/maps"), _READ_FILE & handled)
        _allow_path(ruleset_fd, config, handled)
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno() or errno.EPERM
            raise OSError(error, os.strerror(error))
        _syscall(_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0)
    except OSError as exc:
        raise SystemExit("hosted filesystem isolation could not be applied") from exc
    finally:
        if ruleset_fd is not None:
            os.close(ruleset_fd)


def main() -> None:
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    env = os.environ.copy()
    argv = validate_isolated_command(command, env)
    _vault, config, _control, _secrets, _canary = _validated_paths(env)
    apply_landlock(env)
    os.chdir(config)
    os.execvp(argv[0], argv)


if __name__ == "__main__":  # pragma: no cover - exercised as a subprocess wrapper
    main()
