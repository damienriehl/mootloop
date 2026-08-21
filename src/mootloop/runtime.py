"""Execution trust-mode vocabulary shared across runtime boundaries."""

from enum import StrEnum


class RuntimeMode(StrEnum):
    LOCAL = "local"
    DEV = "dev"
    HOSTED = "hosted"


RUNTIME_MODE_ENV = "MOOTLOOP_RUNTIME_MODE"


def validate_runtime_mode(value: RuntimeMode | str) -> RuntimeMode:
    try:
        return RuntimeMode(value)
    except ValueError as exc:
        raise ValueError("runtime mode must be one of: local, dev, hosted") from exc
