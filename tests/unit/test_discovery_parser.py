"""Unit tests for the deterministic served-discovery parser.

Fixtures cover the realistic shapes: a full MN caption + instructions + definitions
preamble that must be skipped, a rog set with a compound (subpart) interrogatory,
an RFP set with 2015-amendment proportionality wording, an RFA set as a plain
numbered list under a heading, and a numbering-gap document.
"""

from __future__ import annotations

from pathlib import Path

from mootloop.discovery_parser import parse_discovery_document, save_requests
from mootloop.models.common import DocId
from mootloop.models.requests import RequestSet, RequestType

SRC = DocId("doc-0123456789abcdef")

# 1) MN caption + instructions + definitions preamble (skipped), verbose markers,
#    one compound interrogatory with lettered subparts.
ROG_WITH_PREAMBLE = """\
STATE OF MINNESOTA                                     DISTRICT COURT
COUNTY OF HENNEPIN                         FOURTH JUDICIAL DISTRICT

Northfield Widgets LLC,
                Plaintiff,
v.                                         PLAINTIFF'S FIRST SET OF
                                           INTERROGATORIES
Granite Supply Co.,
                Defendant.

INSTRUCTIONS

1. These interrogatories are continuing in nature under Rule 33.01.
2. If you object to any interrogatory, state the basis with particularity.

DEFINITIONS

1. "Document" means any writing as defined in Minn. R. Civ. P. 34.
2. "You" means Granite Supply Co. and its agents.

INTERROGATORY NO. 1: State your full legal name and principal place of business.

INTERROGATORY NO. 2: Identify each person who:
(a) negotiated the supply contract;
(b) approved the purchase order; and
(c) has knowledge of the alleged breach.

INTERROGATORY NO. 3: Describe in detail the circumstances of the delivery that is
the subject of this action, including all relevant dates and quantities.
"""

# 2) Standard rog set with a compound interrogatory (subparts), minimal preamble.
ROG_SUBPARTS = """\
INTERROGATORY NO. 1: State the date each shipment was tendered for delivery.

INTERROGATORY NO. 2: For each nonconforming shipment, identify:
(a) the purchase order number;
(b) the quantity delivered; and
(c) the nature of the nonconformity.
"""

# 3) RFP set with 2015-amendment proportionality wording, verbose markers.
RFP_2015 = """\
REQUEST FOR PRODUCTION NO. 1: All documents concerning the supply contract,
proportional to the needs of the case considering the amount in controversy.

REQUEST FOR PRODUCTION NO. 2: All communications between the parties relating to
delivery schedules, without regard to any burden you contend is disproportionate.

REQUEST FOR PRODUCTION NO. 3: All records of payment for the shipments at issue.
"""

# 4) RFA set as a plain numbered list under a plural heading (fallback path).
RFA_NUMBERED = """\
Preliminary statement that should be ignored entirely.

REQUESTS FOR ADMISSION

1. Admit that you entered into the supply contract dated January 3, 2026.
2. Admit that the March shipment arrived after the contractual deadline.
3. Admit that you received the notice of nonconformity.
"""

# 5) Numbering gap (1, 2, 4) — request 3 is missing.
ROG_GAP = """\
INTERROGATORY NO. 1: State your name.

INTERROGATORY NO. 2: State your address.

INTERROGATORY NO. 4: State your phone number.
"""


def test_preamble_skipped_and_verbose_markers_parsed() -> None:
    report = parse_discovery_document(ROG_WITH_PREAMBLE, RequestType.INTERROGATORY, SRC)
    top = [i for i in report.request_set.items if i.subpart is None]
    assert [i.number for i in top] == [1, 2, 3]
    assert [i.request_id for i in top] == ["ROG-1", "ROG-2", "ROG-3"]
    # definitions / instructions "1." "2." lines were not captured as requests
    assert "continuing in nature" not in top[0].text
    assert top[0].text == "State your full legal name and principal place of business."
    assert report.warnings == []


def test_compound_interrogatory_emits_subparts() -> None:
    report = parse_discovery_document(ROG_WITH_PREAMBLE, RequestType.INTERROGATORY, SRC)
    subs = [i for i in report.request_set.items if i.number == 2 and i.subpart]
    assert [i.request_id for i in subs] == ["ROG-2(a)", "ROG-2(b)", "ROG-2(c)"]
    assert subs[0].text == "negotiated the supply contract;"
    assert subs[2].text == "has knowledge of the alleged breach."
    # parent keeps the full text intact, including the subpart clauses
    parent = next(i for i in report.request_set.items if i.number == 2 and not i.subpart)
    assert "(a) negotiated the supply contract" in parent.text


def test_rog_subparts_counts() -> None:
    report = parse_discovery_document(ROG_SUBPARTS, RequestType.INTERROGATORY, SRC)
    ids = [i.request_id for i in report.request_set.items]
    assert ids == ["ROG-1", "ROG-2", "ROG-2(a)", "ROG-2(b)", "ROG-2(c)"]
    assert report.warnings == []


def test_rfp_2015_wording() -> None:
    report = parse_discovery_document(RFP_2015, RequestType.RFP, SRC, set_number=1)
    assert report.request_set.request_type == RequestType.RFP
    assert [i.request_id for i in report.request_set.items] == ["RFP-1", "RFP-2", "RFP-3"]
    assert "proportional to the needs of the case" in report.request_set.items[0].text
    assert report.warnings == []


def test_rfa_numbered_list_fallback() -> None:
    report = parse_discovery_document(RFA_NUMBERED, RequestType.RFA, SRC)
    items = report.request_set.items
    assert [i.request_id for i in items] == ["RFA-1", "RFA-2", "RFA-3"]
    assert items[0].text.startswith("Admit that you entered into the supply contract")
    # preliminary statement before the heading is dropped
    assert all("Preliminary statement" not in i.text for i in items)
    assert report.warnings == []


def test_numbering_gap_warns_never_drops() -> None:
    report = parse_discovery_document(ROG_GAP, RequestType.INTERROGATORY, SRC)
    assert [i.number for i in report.request_set.items] == [1, 2, 4]
    assert any("missing ROG number(s) 3" in w for w in report.warnings)


def test_empty_document_warns() -> None:
    report = parse_discovery_document("nothing to see here", RequestType.RFA, SRC)
    assert report.request_set.items == []
    assert any("no requests parsed" in w for w in report.warnings)


def test_set_number_flows_to_items_and_filename() -> None:
    report = parse_discovery_document(RFP_2015, RequestType.RFP, SRC, set_number=2)
    assert all(i.set_number == 2 for i in report.request_set.items)
    assert report.request_set.set_number == 2


def test_save_requests_roundtrip(tmp_path: Path) -> None:
    from mootloop.vault import create_vault
    from tests.conftest import make_matter

    vault = tmp_path / "vault"
    create_vault(vault, make_matter(), registry_path=tmp_path / "canaries.json")
    report = parse_discovery_document(RFP_2015, RequestType.RFP, SRC, set_number=3)

    path = save_requests(vault, report.request_set)
    assert path == vault / "requests" / "rfp-set03.json"
    reloaded = RequestSet.model_validate_json(path.read_text(encoding="utf-8"))
    assert [i.request_id for i in reloaded.items] == ["RFP-1", "RFP-2", "RFP-3"]
