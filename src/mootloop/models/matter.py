"""`MatterConfig` — the schema for a vault's `matter.yaml`."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mootloop.models.common import MATTER_ID_PATTERN, VersionedModel
from mootloop.models.config import BudgetTier, PipelineStrategy, RunConfigOverlay, RunMode

SCHEMA_VERSION = "1.0"

Forum = Literal["state", "federal"]
PartyRole = Literal["plaintiff", "defendant", "third-party"]
Side = Literal["plaintiff", "defendant"]
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
    oc_associate: bool = True
    oc_partner: bool = True
    judge: bool = True
    rubric_judge: bool = True

    @model_validator(mode="before")
    @classmethod
    def _import_legacy_keys(cls, value: object) -> object:
        """Translate retired selection keys without keeping them user-facing."""
        if not isinstance(value, Mapping):
            return value
        imported = dict(value)
        opposing = imported.pop("opposing_counsel", None)
        if opposing is not None:
            imported.setdefault("oc_associate", opposing)
            imported.setdefault("oc_partner", opposing)
        imported.pop("cite_checker", None)
        return imported


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


class Attorney(_Model):
    """The signing attorney's block for the signature + verification pages (plan D7)."""

    name: str
    firm: str = ""
    address: str = ""
    email: str = ""
    phone: str = ""
    bar_number: str = ""


class Retention(_Model):
    retention_class: str = Field(min_length=1, max_length=128)
    destruction_date: date | None = None
    litigation_hold: bool = False


class MatterConfig(VersionedModel):
    """Top-level `matter.yaml` schema. Extends VersionedModel (extra=forbid)."""

    @model_validator(mode="before")
    @classmethod
    def require_litigation_fields(cls, value: object) -> object:
        if isinstance(value, Mapping) and value.get("matter_kind", "litigation") == "litigation":
            missing = [name for name in ("caption", "parties", "our_side") if name not in value]
            if missing:
                raise ValueError(f"litigation requires {', '.join(missing)}")
        return value

    matter_kind: Literal["litigation", "advisory"] = "litigation"
    client: str | None = None
    objective: str | None = None
    matter_id: MatterIdStr
    caption: Caption | None = None
    jurisdiction: Jurisdiction
    parties: list[Party] = Field(default_factory=list)
    our_side: Side | None = None
    deadlines: list[Deadline] = Field(default_factory=list)
    personas: Personas = Field(default_factory=Personas)
    pipeline_strategy: PipelineStrategy = "thin-full"
    panels: Panels = Field(default_factory=Panels)
    gates: list[Gate] = Field(default_factory=_default_gates)
    # Run mode default (plan D12 precedence: defaults -> matter.yaml -> --mode flag).
    run_mode: RunMode = "autonomous"
    budget: Budget = Field(default_factory=Budget)
    attorney: Attorney | None = None
    retention: Retention
    # Runtime choices stay in a dedicated overlay; caption/parties/deadlines remain
    # case metadata and are never merged into run behavior.
    run_config: RunConfigOverlay | None = None

    @model_validator(mode="after")
    def validate_matter_kind(self) -> MatterConfig:
        if self.schema_version not in ("1.0", "1.1"):
            raise ValueError("unsupported matter schema version")
        if self.matter_kind == "litigation":
            if (
                self.caption is None
                or self.our_side is None
                or "parties" not in self.model_fields_set
            ):
                raise ValueError("litigation requires caption and our_side")
        elif (
            self.schema_version != "1.1"
            or not self.client
            or not self.client.strip()
            or not self.objective
            or not self.objective.strip()
        ):
            raise ValueError("advisory requires schema 1.1, client and objective")
        elif self.caption is not None or self.our_side is not None or self.parties:
            raise ValueError("advisory matters cannot carry litigation roles or caption")
        return self
