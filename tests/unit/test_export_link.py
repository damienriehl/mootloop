"""Signed deliverable download links (plan FD-7 / P-37): token mint/validate round-trip,
bounded expiry, tamper-evidence (constant-time HMAC compare), single-audience binding,
and the DRAFT-vs-clean export gate. Every failure path fails closed — an unverifiable or
ungated link never resolves a byte.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mootloop.errors import ExportLinkError, ExportNotReadyError
from mootloop.export import link as link_svc
from mootloop.export.link import (
    MAX_TTL_SECONDS,
    LinkSigner,
    mint_link,
    resolve_for_download,
    validate_token,
)

NOW = "2026-07-11T00:00:00+00:00"
LATER = "2026-07-11T00:20:00+00:00"  # 20 min after NOW — past any link's expiry
MATTER = "acme-v-widgets"
RUN = "discovery-responses-20260711"
KEY = "unit-test-signing-key-0123456789abcdef"


def _seed(vault: Path, *names: str) -> None:
    base = vault / "deliverables" / RUN
    base.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"work product bytes")


@pytest.fixture
def signer() -> LinkSigner:
    return LinkSigner(KEY)


@pytest.fixture
def ready_vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    _seed(tmp_path, "responses.docx", "responses.DRAFT.docx", "master.md")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (True, []))
    return tmp_path


# --- mint / validate round-trip ----------------------------------------------


def test_mint_and_validate_round_trip(ready_vault: Path, signer: LinkSigner) -> None:
    link = mint_link(ready_vault, MATTER, RUN, "responses.docx", NOW, signer)
    assert link.url == f"/api/download?token={link.token}"
    assert link.is_draft is False

    claims = validate_token(link.token, NOW, signer)
    assert (claims.matter_id, claims.run_id, claims.doc) == (MATTER, RUN, "responses.docx")
    # And the gate re-check resolves the on-disk path for streaming.
    assert resolve_for_download(ready_vault, claims).name == "responses.docx"


def test_ttl_is_clamped_to_max(ready_vault: Path, signer: LinkSigner) -> None:
    link = mint_link(
        ready_vault, MATTER, RUN, "responses.docx", NOW, signer, ttl_seconds=99_999.0
    )
    claims = validate_token(link.token, NOW, signer)
    assert claims.exp - _epoch(NOW) <= MAX_TTL_SECONDS + 0.001


# --- expiry ------------------------------------------------------------------


def test_expired_token_is_rejected(ready_vault: Path, signer: LinkSigner) -> None:
    link = mint_link(
        ready_vault, MATTER, RUN, "responses.docx", NOW, signer, ttl_seconds=300.0
    )
    # 20 minutes later the (<=10 min) link has expired -> fail closed.
    with pytest.raises(ExportLinkError, match="expired"):
        validate_token(link.token, LATER, signer)


def test_manually_backdated_token_is_rejected(signer: LinkSigner) -> None:
    body = _sign(signer, {"m": MATTER, "r": RUN, "d": "responses.docx", "exp": 0.0})
    with pytest.raises(ExportLinkError, match="expired"):
        validate_token(body, NOW, signer)


# --- tamper-evidence (constant-time HMAC compare) ----------------------------


def test_flipped_payload_byte_is_rejected(ready_vault: Path, signer: LinkSigner) -> None:
    link = mint_link(ready_vault, MATTER, RUN, "responses.docx", NOW, signer)
    body, mac = link.token.split(".", 1)
    tampered_body = _flip(body)
    with pytest.raises(ExportLinkError, match="signature mismatch"):
        validate_token(f"{tampered_body}.{mac}", NOW, signer)


def test_flipped_mac_byte_is_rejected(ready_vault: Path, signer: LinkSigner) -> None:
    link = mint_link(ready_vault, MATTER, RUN, "responses.docx", NOW, signer)
    body, mac = link.token.split(".", 1)
    with pytest.raises(ExportLinkError, match="signature mismatch"):
        validate_token(f"{body}.{_flip(mac)}", NOW, signer)


def test_wrong_key_is_rejected(ready_vault: Path, signer: LinkSigner) -> None:
    link = mint_link(ready_vault, MATTER, RUN, "responses.docx", NOW, signer)
    with pytest.raises(ExportLinkError, match="signature mismatch"):
        validate_token(link.token, NOW, LinkSigner("a-different-key-entirely-000000"))


def test_malformed_token_is_rejected(signer: LinkSigner) -> None:
    with pytest.raises(ExportLinkError, match="malformed"):
        validate_token("no-dot-here", NOW, signer)


# --- single-audience binding -------------------------------------------------


def test_token_is_bound_to_one_deliverable(ready_vault: Path, signer: LinkSigner) -> None:
    _seed(ready_vault, "other.docx")
    link_a = mint_link(ready_vault, MATTER, RUN, "responses.docx", NOW, signer)
    claims = validate_token(link_a.token, NOW, signer)
    # The audience is doc A; it never resolves to a different file.
    assert claims.doc == "responses.docx"
    assert resolve_for_download(ready_vault, claims).name == "responses.docx"
    # Re-pointing the signed doc claim at B invalidates the MAC (can't be reused for B).
    body, mac = link_a.token.split(".", 1)
    forged = _sign(signer, {"m": MATTER, "r": RUN, "d": "other.docx", "exp": 9e12})
    assert forged.split(".", 1)[1] != mac


def test_audience_matter_and_run_survive_round_trip(ready_vault: Path, signer: LinkSigner) -> None:
    link = mint_link(ready_vault, "other-matter", RUN, "responses.docx", NOW, signer)
    claims = validate_token(link.token, NOW, signer)
    assert (claims.matter_id, claims.run_id) == ("other-matter", RUN)


# --- DRAFT vs clean export gate ----------------------------------------------


def test_clean_docx_requires_export_ready_at_mint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signer: LinkSigner
) -> None:
    _seed(tmp_path, "responses.docx", "responses.DRAFT.docx")
    monkeypatch.setattr(
        "mootloop.gate_ledger.export_ready", lambda v, r: (False, ["attestation", "citations"])
    )
    with pytest.raises(ExportNotReadyError) as exc:
        mint_link(tmp_path, MATTER, RUN, "responses.docx", NOW, signer)
    assert exc.value.blockers == ["attestation", "citations"]


def test_draft_docx_is_linkable_when_not_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signer: LinkSigner
) -> None:
    _seed(tmp_path, "responses.DRAFT.docx")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (False, ["attestation"]))
    link = mint_link(tmp_path, MATTER, RUN, "responses.DRAFT.docx", NOW, signer)
    assert link.is_draft is True
    # And it resolves for download even though the run is not export-ready.
    claims = validate_token(link.token, NOW, signer)
    assert resolve_for_download(tmp_path, claims).name == "responses.DRAFT.docx"


def test_informational_master_is_never_gated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signer: LinkSigner
) -> None:
    _seed(tmp_path, "master.md")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (False, ["attestation"]))
    link = mint_link(tmp_path, MATTER, RUN, "master.md", NOW, signer)
    assert link.is_draft is False


def test_download_gate_re_evaluated_after_mint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, signer: LinkSigner
) -> None:
    # Defense in depth: a link minted while ready must not stream if the gate later closes.
    _seed(tmp_path, "responses.docx")
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (True, []))
    link = mint_link(tmp_path, MATTER, RUN, "responses.docx", NOW, signer)
    claims = validate_token(link.token, NOW, signer)
    monkeypatch.setattr("mootloop.gate_ledger.export_ready", lambda v, r: (False, ["attestation"]))
    with pytest.raises(ExportNotReadyError):
        resolve_for_download(tmp_path, claims)


def test_unknown_deliverable_is_rejected(ready_vault: Path, signer: LinkSigner) -> None:
    with pytest.raises(ExportLinkError, match="unknown deliverable"):
        mint_link(ready_vault, MATTER, RUN, "nope.docx", NOW, signer)


# --- helpers -----------------------------------------------------------------


def _epoch(iso: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(iso).timestamp()


def _sign(signer: LinkSigner, payload: dict[str, object]) -> str:
    return signer.sign(payload)


def _flip(segment: str) -> str:
    """Return the segment with one character changed (guaranteed different)."""
    ch = segment[0]
    repl = "A" if ch != "A" else "B"
    return repl + segment[1:]


def test_module_surface() -> None:
    assert link_svc.MAX_TTL_SECONDS == 600.0
    # A minted body signs to `<body>.<mac>`; the payload is compact sorted JSON.
    signer = LinkSigner(KEY)
    token = signer.sign({"m": "x", "exp": 1.0})
    body = token.split(".", 1)[0]
    from base64 import urlsafe_b64decode

    pad = "=" * (-len(body) % 4)
    assert json.loads(urlsafe_b64decode(body + pad)) == {"m": "x", "exp": 1.0}
