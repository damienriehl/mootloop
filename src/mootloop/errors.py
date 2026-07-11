"""Exception hierarchy. Reserved for infra/precondition errors; gate *results* are
artifact states, not exceptions."""

from __future__ import annotations


class MootloopError(Exception):
    """Base class for all MootLoop errors."""


class VaultBoundaryError(MootloopError):
    """A path escaped its vault root, or a vault overlapped the repo tree."""


class MatterConfigError(MootloopError):
    """matter.yaml failed to parse or validate. Message names each bad field."""


class LockHeldError(MootloopError):
    """A run lock is held by a live process (or another host) and was not overridden."""
