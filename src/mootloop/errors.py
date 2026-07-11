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


class IngestError(MootloopError):
    """A corpus ingest precondition failed (bad source dir, unresolvable tag, …)."""


class FactError(MootloopError):
    """A fact-store precondition failed (unknown fact id, unresolvable provenance, …)."""


class TaskConfigError(MootloopError):
    """A task-adapter config failed to load or validate. Message names each bad field."""


class OrchestratorError(MootloopError):
    """An orchestrator precondition failed (unknown run, unschedulable turn, …)."""


class RubricLockError(MootloopError):
    """A LOCKED rubric's content no longer matches its recorded hash. Changing a
    locked rubric requires shipping a new version file — never editing in place."""


class BudgetError(MootloopError):
    """A budget precondition failed (unknown tier/model, un-estimable run, …)."""
