"""Portable, input-bound prepared responses for credential-free scripted playback."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from mootloop.models.common import StrictModel, VersionedModel
from mootloop.models.run import OUTPUT_SCHEMAS


class ReplayResponse(StrictModel):
    unit_id: str
    stage: str
    occurrence: int = Field(ge=1)
    output_schema_name: str
    output: dict[str, Any]

    @model_validator(mode="after")
    def validate_output(self) -> ReplayResponse:
        schema = OUTPUT_SCHEMAS.get(self.output_schema_name)
        if schema is None:
            raise ValueError("unknown replay output schema")
        schema.model_validate(self.output)
        return self


class PreparedReplay(VersionedModel):
    schema_version: Literal["1.0"] = "1.0"
    task: str
    document_input_sha256: dict[str, str] = Field(default_factory=dict)
    discovery_input_sha256: dict[str, str] = Field(default_factory=dict)
    responses: list[ReplayResponse] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_responses(self) -> PreparedReplay:
        if bool(self.document_input_sha256) == bool(self.discovery_input_sha256):
            raise ValueError("replay requires exactly one frozen input family")
        keys = [(r.unit_id, r.stage, r.occurrence) for r in self.responses]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate replay response identity")
        return self
