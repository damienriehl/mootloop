"""Discovery-request vocabulary: request types, the per-request work item, a parsed
`RequestSet`, and the `ParseReport` a parse returns (set + non-fatal warnings).

Request IDs are opponent-owned and immutable (plan D12): ``ROG-3`` / ``RFP-12`` /
``RFA-7``, with a lettered subpart suffix like ``ROG-3(a)``. Every downstream anchor
keys on these, so they never encode our set number — that rides as a field.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from mootloop.models.common import DocId, RequestId, StrictModel, VersionedModel

SCHEMA_VERSION = "1.0"


class RequestType(StrEnum):
    """The three served-discovery request families."""

    INTERROGATORY = "interrogatory"
    RFP = "rfp"
    RFA = "rfa"


# (short filename code, canonical ID prefix, verbose marker label, plural heading)
_TYPE_META: dict[RequestType, tuple[str, str, str, str]] = {
    RequestType.INTERROGATORY: ("rog", "ROG", "INTERROGATORY", "INTERROGATORIES"),
    RequestType.RFP: ("rfp", "RFP", "REQUEST FOR PRODUCTION", "REQUESTS FOR PRODUCTION"),
    RequestType.RFA: ("rfa", "RFA", "REQUEST FOR ADMISSION", "REQUESTS FOR ADMISSION"),
}


def type_code(request_type: RequestType) -> str:
    """Short filename code (``rog`` / ``rfp`` / ``rfa``)."""
    return _TYPE_META[request_type][0]


def id_prefix(request_type: RequestType) -> str:
    """Canonical request-ID prefix (``ROG`` / ``RFP`` / ``RFA``)."""
    return _TYPE_META[request_type][1]


def marker_label(request_type: RequestType) -> str:
    """Verbose per-request marker label (``INTERROGATORY`` …)."""
    return _TYPE_META[request_type][2]


def heading_label(request_type: RequestType) -> str:
    """Plural section heading used for numbered-list fallback parsing."""
    return _TYPE_META[request_type][3]


def make_request_id(
    request_type: RequestType, number: int, subpart: str | None = None
) -> RequestId:
    """Build a canonical request ID, e.g. ``ROG-3`` or ``ROG-3(a)``."""
    base = f"{id_prefix(request_type)}-{number}"
    return RequestId(f"{base}({subpart})" if subpart else base)


class RequestItem(StrictModel):
    """One served request (or a subpart of one) as a unit of work.

    ``subpart`` is ``None`` for a top-level request and a single lowercase letter
    for a compound subpart, whose ``text`` is the subpart clause; the parent item
    retains the full request text.
    """

    request_id: RequestId
    set_number: int = 1
    number: int
    subpart: str | None = None
    text: str
    source_doc: DocId


class RequestSet(VersionedModel):
    """A parsed set of served requests of one type from one served document."""

    schema_version: str = SCHEMA_VERSION
    request_type: RequestType
    set_number: int
    title: str
    items: list[RequestItem] = Field(default_factory=list)


class ParseReport(StrictModel):
    """A parse result: the ``RequestSet`` plus non-fatal warnings (numbering gaps,
    duplicates). Warnings never live on the persisted set — a parse never silently
    drops a request."""

    request_set: RequestSet
    warnings: list[str] = Field(default_factory=list)
