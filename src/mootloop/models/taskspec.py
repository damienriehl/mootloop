"""`TaskSpec` — the on-ramp's product (plan FE-3 / FD-10 thin on-ramp).

A ``TaskSpec`` is what a begin-task on-ramp produces and ``start_run`` consumes: the
resolved task (a registry key, or ``None`` when the intent could not be mapped to a
runnable task), the source lane, and the FOLIO/UTBMS breadcrumbs a later run may carry
for grounding. Per FD-10 the thin on-ramp ships only the fields the first run consumes;
the remaining on-ramp lanes (wizard/suggestion) and richer refs (board-curation,
synthesized-adapter) land as those features do.

Freeform resolution is DETERMINISTIC in v1 (keyword/registry match); LLM
concept-resolution lands in FE-3.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Literal

from pydantic import Field, model_validator

from mootloop.models.common import (
    MatterId,
    RubricId,
    TaskSpecId,
    TaskSpecLockId,
    VersionedModel,
)

SCHEMA_VERSION = "1.0"

# The on-ramp lanes that can produce a TaskSpec (plan P-30). Only ``freeform`` is wired
# in the thin on-ramp; ``wizard``/``suggestion`` are reserved for FE-3+.
SourceLane = Literal["freeform", "wizard", "suggestion"]
LockSource = Literal["human"]
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_sha256(value: object) -> str:
    """SHA-256 of deterministic UTF-8 JSON used by TaskSpec lock records."""
    raw = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def task_spec_sha256(spec: TaskSpec) -> str:
    """Digest the complete canonical TaskSpec, including its schema version."""
    return canonical_sha256(spec.model_dump(mode="json"))


class TaskSpec(VersionedModel):
    """A resolved (or unresolved) begin-task specification, persisted append-only.

    ``task`` is a registered task-adapter key (``discovery-responses``) when the intent
    resolved, or ``None`` when it did not — an unresolved spec is recorded for the audit
    trail but is NOT runnable (``runnable`` is ``False``): no run can start from it until
    a later lane resolves the concept.
    """

    schema_version: str = SCHEMA_VERSION
    task_spec_id: TaskSpecId
    matter_id: MatterId
    task: str | None
    source_lane: SourceLane
    intent_text: str
    folio_iri: str | None = None
    folio_label: str | None = None
    utbms: str | None = None
    request_set_refs: list[str] = Field(default_factory=list)
    created_at: str

    @property
    def resolved(self) -> bool:
        """Whether the on-ramp resolved this spec to a registered task key."""
        return self.task is not None

    @property
    def runnable(self) -> bool:
        """Legacy spec-level eligibility; launch additionally requires a human lock."""
        return self.resolved


class TaskSpecLock(VersionedModel):
    """One append-only human approval of exact TaskSpec launch inputs."""

    schema_version: str = SCHEMA_VERSION
    task_spec_lock_id: TaskSpecLockId
    lock_version: int = Field(ge=1)
    task_spec_id: TaskSpecId
    matter_id: MatterId
    task: str
    task_spec_sha256: str
    adapter_locator: str
    adapter_sha256: str
    rubric_id: RubricId
    rubric_locator: str
    rubric_sha256: str
    rubric_lock_locator: str
    rubric_lock_sha256: str
    rubric_recorded_sha256: str
    locked_by: str
    source: LockSource = "human"
    locked_at: str
    record_sha256: str

    def digest_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"record_sha256"})

    @model_validator(mode="after")
    def validate_integrity(self) -> TaskSpecLock:
        digest_fields = (
            "task_spec_sha256",
            "adapter_sha256",
            "rubric_sha256",
            "rubric_lock_sha256",
            "rubric_recorded_sha256",
            "record_sha256",
        )
        for field in digest_fields:
            if not _SHA256_RE.fullmatch(str(getattr(self, field))):
                raise ValueError(f"{field} must be exactly 64 lowercase hexadecimal characters")
        if not self.locked_by.strip():
            raise ValueError("locked_by must identify the human actor")
        if not self.locked_at.strip():
            raise ValueError("locked_at must identify the approval time")
        if self.rubric_sha256 != self.rubric_recorded_sha256:
            raise ValueError("rubric digest does not match the locked rubric sidecar")
        if self.record_sha256 != canonical_sha256(self.digest_payload()):
            raise ValueError("TaskSpecLock record digest does not match its content")
        return self
