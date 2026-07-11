"""`MatterConfig` — the schema for a vault's `matter.yaml`."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from mootloop.models.common import MATTER_ID_PATTERN, VersionedModel

SCHEMA_VERSION = "1.0"

Forum = Literal["state", "federal"]
PartyRole = Literal["plaintiff", "defendant", "third-party"]
Side = Literal["plaintiff", "defendant"]
BudgetTier = Literal["no-budget", "moderate", "low"]
GateMode = Literal["hard-human", "policy-delegable"]

MatterIdStr = Annotated[str, Field(pattern=MATTER_ID_PATTERN)]


class _Model(BaseModel):
    """Sub-models share the strict config but are not independently persisted."""

    model_config = ConfigDict(extra="forbid")


class Caption(_Model):
    court_name: str
    case_number: str
    county: str
    judge_name: str | None = None


class Jurisdiction(_Model):
    state: str
    forum: Forum


class Party(_Model):
    name: str
    role: PartyRole


class Deadline(_Model):
    name: str
    date: date
    rule: str


class Personas(_Model):
    associate: bool = True
    partner: bool = True
    opposing_counsel: bool = True
    judge: bool = True
    rubric_judge: bool = True
    cite_checker: bool = True


class Panels(_Model):
    judges: int = 3
    jurors: int = 0
    jury_enabled: bool = False


class Gate(_Model):
    name: str
    mode: GateMode


def _default_gates() -> list[Gate]:
    return [
        Gate(name="privilege", mode="hard-human"),
        Gate(name="rfa_disposition", mode="hard-human"),
        Gate(name="attestation", mode="hard-human"),
        Gate(name="objection_posture", mode="policy-delegable"),
        Gate(name="unsupported_assertion", mode="policy-delegable"),
    ]


class Budget(_Model):
    tier: BudgetTier = "moderate"
    hard_cap_usd: float | None = None


class Retention(_Model):
    retention_class: str
    destruction_date: date | None = None
    litigation_hold: bool = False


class MatterConfig(VersionedModel):
    """Top-level `matter.yaml` schema. Extends VersionedModel (extra=forbid)."""

    matter_id: MatterIdStr
    caption: Caption
    jurisdiction: Jurisdiction
    parties: list[Party]
    our_side: Side
    deadlines: list[Deadline] = Field(default_factory=list)
    personas: Personas = Field(default_factory=Personas)
    panels: Panels = Field(default_factory=Panels)
    gates: list[Gate] = Field(default_factory=_default_gates)
    budget: Budget = Field(default_factory=Budget)
    retention: Retention
