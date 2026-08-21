"""Durable commitment for one protected corpus conversion."""

from __future__ import annotations

import re

from pydantic import model_validator

from mootloop.models.common import (
    DocId,
    MatterProvenanced,
    VersionedModel,
    canonical_json_sha256,
)

SCHEMA_VERSION = "1.0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ConversionReceipt(MatterProvenanced, VersionedModel):
    """Exact input, output, converter, and actor committed before manifest promotion."""

    schema_version: str = SCHEMA_VERSION
    conversion_id: str
    doc_id: DocId
    input_sha256: str
    input_format: str
    output_sha256: str
    normalized_path: str
    converter: str
    converter_image: str
    converter_commit: str
    converted_at: str
    actor: str
    receipt_sha256: str

    @model_validator(mode="after")
    def validate_commitment(self) -> ConversionReceipt:
        for field_name in ("input_sha256", "output_sha256", "receipt_sha256"):
            if not _SHA256_RE.fullmatch(getattr(self, field_name)):
                raise ValueError(f"{field_name} must be a lowercase SHA-256")
        if self.receipt_sha256 != conversion_receipt_sha256(self):
            raise ValueError("receipt_sha256 does not match the conversion receipt")
        return self


def conversion_receipt_sha256(receipt: ConversionReceipt) -> str:
    payload = receipt.model_dump(mode="json", exclude={"receipt_sha256"})
    return canonical_json_sha256(payload)
