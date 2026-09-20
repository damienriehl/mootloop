"""Evidence checklist for separately prepared public source packets."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from mootloop.models.demo import SourceReference


def validate_source_packet(sources: Sequence[SourceReference], cutoff: date) -> None:
    ids = [source.source_id for source in sources]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate source identity")
    eligible = [source for source in sources if source.eligible_at(cutoff)]
    if not any(
        source.classification in {"record", "allegation", "disputed"} for source in eligible
    ):
        raise ValueError("source packet needs eligible pre-cutoff record material")
    if not all(
        source.retrieved_on >= (source.available_on or source.retrieved_on) for source in sources
    ):
        raise ValueError("source cannot be retrieved before it was available")
