"""Structural invariants for the Phase 4 citation/fabrication layer (plan D3/H8/H9)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from mootloop.citations.verify import CITATOR_DISCLOSURE, verify_all
from mootloop.models.citations import (
    VerificationRecord,
    VerificationStatus,
    fold_ledger,
    make_citation_id,
)

pytestmark = pytest.mark.invariant

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src" / "mootloop"
NOW = "2026-07-11T00:00:00+00:00"

# Secret-shaped tokens must never appear in a run/law/research artifact (plan D3).
_SECRET_RE = re.compile(r"(?i)\b(Token|Bearer)\s+\S+|\bsk-[A-Za-z0-9_\-]{8,}")


def test_only_http_module_imports_httpx() -> None:
    """Egress is contained to citations/http.py (plan H9): nothing else imports httpx."""
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        if path.name == "http.py" and path.parent.name == "citations":
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(import httpx|from httpx\b)", text, re.MULTILINE):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"only citations/http.py may import httpx; offenders: {offenders}"


def test_citator_disclosure_names_a_citator() -> None:
    lowered = CITATOR_DISCLOSURE.lower()
    assert "citator" in lowered
    assert "keycite" in lowered or "shepard" in lowered


def _rec(cid, status: VerificationStatus) -> VerificationRecord:
    return VerificationRecord(
        citation_id=cid, status=status, source="courtlistener", verified_at=NOW
    )


def test_ledger_fold_is_deterministic() -> None:
    cid = make_citation_id("x")
    records = [
        _rec(cid, VerificationStatus.PENDING),
        _rec(cid, VerificationStatus.VERIFIED),
    ]
    now = datetime.fromisoformat(NOW)
    first = fold_ledger(records, now=now, max_cache_age_days=30)
    second = fold_ledger(records, now=now, max_cache_age_days=30)
    assert first[cid].model_dump() == second[cid].model_dump()
    assert first[cid].status == VerificationStatus.VERIFIED  # last write wins


def test_no_secret_shaped_strings_in_run_artifacts(tmp_path: Path, monkeypatch) -> None:
    """A token in the environment must never leak into a persisted artifact (plan D3/H8)."""
    monkeypatch.setenv("COURTLISTENER_TOKEN", "deadbeef" * 5)  # 40-hex-shaped token
    from mootloop.citations.extract import extract_citations

    case = next(c for c in extract_citations("Roe v. Wade, 410 U.S. 113 (1973)"))
    payload = [
        {
            "citation": case.normalized,
            "normalized_citations": [case.normalized],
            "status": 404,
            "clusters": [],
        }
    ]
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    verify_all(tmp_path, [case], NOW, transport=transport)

    for path in tmp_path.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert "deadbeef" not in text, f"token leaked into {path}"
        assert not _SECRET_RE.search(text), f"secret-shaped string in {path}"
