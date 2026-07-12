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


class SeatLimitError(MootloopError):
    """The headless Claude subscription hit a seat/rate limit. The run pauses and the
    driver reschedules it for a later resume — the work is not lost (plan FE-1)."""


class AuthError(MootloopError):
    """Headless Claude authentication failed (a bad or expired OAuth token). The run
    needs attention; the driver stops retrying and raises a notification (plan FE-1)."""


class TurnError(MootloopError):
    """A headless turn failed for any reason other than a seat limit or auth failure.
    Its message is redacted — a raw token never appears in the exception text."""


class QueueError(MootloopError):
    """A driver-queue precondition failed (bad lane, malformed work item, …)."""


class DriverError(MootloopError):
    """A driver-loop precondition failed (unresolvable matter, missing run dir, …)."""


class BackupError(MootloopError):
    """A hosted-backup precondition failed: the destination is inside a background-sync
    folder or a git repo, a consistent snapshot point could not be acquired, or the
    tar readback did not list the expected members (plan FD-6 hosted-backup gate)."""


class TaskSpecError(MootloopError):
    """A begin-task on-ramp precondition failed (unknown TaskSpec id, malformed spec
    store, …). An *unresolved* intent is a valid TaskSpec (``task=None``), never an
    error — this is reserved for genuine preconditions (plan FE-2.5)."""


class ExportLinkError(MootloopError):
    """A signed download link could not be minted or validated: an unknown deliverable,
    or a tampered/expired token. Fails closed — an unverifiable link never streams a
    byte (plan FD-7 / P-37)."""


class ExportNotReadyError(ExportLinkError):
    """A clean (non-DRAFT) deliverable was requested but the run is not export-ready
    (``gate_ledger.export_ready`` is false). Carries the blocking gate names so the UI
    can explain why. DRAFT deliverables are never gated this way (plan P-37)."""

    def __init__(self, deliverable: str, blockers: list[str]) -> None:
        self.deliverable = deliverable
        self.blockers = blockers
        super().__init__(
            f"deliverable {deliverable!r} is not export-ready; blockers: "
            + (", ".join(blockers) or "(unknown)")
        )
