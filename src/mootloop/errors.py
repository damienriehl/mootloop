"""Exception hierarchy. Reserved for infra/precondition errors; gate *results* are
artifact states, not exceptions."""

from __future__ import annotations


class MootloopError(Exception):
    """Base class for all MootLoop errors."""


class VaultBoundaryError(MootloopError):
    """A path escaped its vault root, or a vault overlapped the repo tree."""


class MatterConfigError(MootloopError):
    """matter.yaml failed to parse or validate. Message names each bad field."""


class MatterNotFoundError(MootloopError):
    """No matter with the requested id exists under the matters-root. Distinct from
    `VaultBoundaryError` (a containment breach) so callers can 404 vs. 400."""


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


class EgressError(MootloopError):
    """An outbound HTTP request violated the egress policy — a host outside the fixed
    allowlist, or a target that did not come from one of our own request builders."""


class CitationError(MootloopError):
    """A citation-verification precondition failed (unknown research request, missing
    fulfillment file, …). Verification *outcomes* are ledger states, not exceptions."""


class DecisionError(MootloopError):
    """An attorney-gate decision precondition failed (unknown decision id, resolving
    an already-resolved decision, a modify with no chosen key, …)."""


class AttestationBlockedError(MootloopError):
    """Attestation was refused: open attorney-gate decisions remain, or the md-master
    deliverable does not yet exist. Attestation reads gate state; it never sets it."""


class ExportError(MootloopError):
    """An export precondition failed (missing master, unreadable reference doc, …)."""


class PandocMissingError(ExportError):
    """pandoc is not on PATH, so DOCX rendering cannot run. The court-formatted
    markdown is still emitted; the DOCX step degrades gracefully (plan Phase 7)."""


class AccessAuthError(MootloopError):
    """Cloudflare Access JWT verification failed — bad signature/alg, wrong
    aud/iss/email, expired, or an unfetchable JWKS. Every failure fails closed;
    the verifier never falls through to "unverified" (plan FD-2)."""


class InternalAuthError(MootloopError):
    """The internal driver/BFF secret was missing, empty, or did not match the
    configured value. Replaces localhost trust on the shared Docker network
    (plan FD-1); a missing secret rejects (fail closed)."""


class AuditWriteError(MootloopError):
    """A hash-chained access-audit append could not be durably written. Callers must
    treat this as fatal and fail closed — a matter-data page view or download that
    cannot be recorded is refused, never served (plan FD-3, threat-model item 13)."""
