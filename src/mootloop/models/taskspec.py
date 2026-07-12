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

from typing import Literal

from pydantic import Field

from mootloop.models.common import MatterId, TaskSpecId, VersionedModel

SCHEMA_VERSION = "1.0"

# The on-ramp lanes that can produce a TaskSpec (plan P-30). Only ``freeform`` is wired
# in the thin on-ramp; ``wizard``/``suggestion`` are reserved for FE-3+.
SourceLane = Literal["freeform", "wizard", "suggestion"]


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
    def runnable(self) -> bool:
        """Whether a run can start from this spec — true iff a task resolved."""
        return self.task is not None
