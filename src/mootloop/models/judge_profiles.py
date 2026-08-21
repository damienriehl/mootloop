"""Bounded public-opinion judge profiles and held-out calibration evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from mootloop.models.common import CitationId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"


class JudgeOpinionRef(StrictModel):
    citation_id: CitationId
    cluster_id: int = Field(gt=0)
    source_url: str
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    disposition: Literal["granted", "denied"]
    calibration_split: Literal["training", "holdout"]


class JudgeCalibration(StrictModel):
    training_examples: int = Field(ge=0)
    holdout_examples: int = Field(ge=0)
    baseline_disposition: Literal["granted", "denied"] | None = None
    correct_holdout_predictions: int = Field(ge=0)
    error_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    maximum_calibrated_error: float = Field(default=0.35, ge=0.0, le=1.0)
    calibrated: bool = False
    limits: list[str] = Field(default_factory=list)


class JudgeProfile(VersionedModel):
    """A deterministic, directional profile distilled from exact public opinion bytes."""

    schema_version: str = SCHEMA_VERSION
    profile_id: str = Field(pattern=r"^judge-profile-[0-9a-f]{16}$")
    judge_name: str = Field(min_length=1, max_length=120)
    jurisdiction_state: str = Field(min_length=1, max_length=80)
    court_name: str = Field(min_length=1, max_length=200)
    built_at: str
    source_query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    opinions: list[JudgeOpinionRef] = Field(max_length=20)
    calibration: JudgeCalibration
    prompt_text: str = Field(min_length=1, max_length=4000)
    directional_only: Literal[True] = True
