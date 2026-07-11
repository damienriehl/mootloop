"""Local citation extraction (plan D8): eyecite over cleaned text, plus a regex
fallback for shapes eyecite misses (Minnesota court rules).

Per D8, ``clean_text(['all_whitespace'])`` runs *before* ``get_citations`` — eyecite
offsets are into the cleaned text. Authority type is classified from the eyecite
object type (``FullCaseCitation`` -> case) and, for statutes/regulations, from the
reporter token (``U.S.C.`` -> federal statute, ``C.F.R.`` -> regulation, ``Minn.
Stat.`` -> state statute). Results are deduped by normalized form, first-seen order
preserved.
"""

from __future__ import annotations

import re
from typing import Any

from eyecite import clean_text, get_citations
from eyecite.models import FullCaseCitation, FullLawCitation

from mootloop.models.citations import AuthorityType, Citation, make_citation_id

# Reporter token -> authority type for statute/regulation FullLawCitations (plan D8).
_LAW_REPORTER_TYPES: dict[str, AuthorityType] = {
    "U.S.C.": AuthorityType.FEDERAL_STATUTE,
    "C.F.R.": AuthorityType.REGULATION,
    "Minn. Stat.": AuthorityType.STATE_STATUTE,
}

# eyecite does not tokenize court-rule cites, so a narrow regex backfills the two MN
# shapes the discovery task relies on (Rules of Civil Procedure + General Rules).
_RULE_RE = re.compile(
    r"\bMinn\.\s*(?:R\.\s*Civ\.\s*P\.|Gen\.\s*R\.\s*Prac\.)\s*\d+(?:\.\d+)?",
)
# Belt-and-suspenders fallback for the statute shape in case eyecite ever misses it.
# Trailing punctuation is excluded so it dedupes with eyecite's own normalized form.
_STATUTE_RE = re.compile(r"\bMinn\.\s*Stat\.\s*§+\s*\d+(?:[.\-][0-9A-Za-z]+)*")


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _corrected(cite: FullCaseCitation | FullLawCitation) -> str:
    """eyecite's normalized cite string (eyecite is untyped — narrow to ``str`` here)."""
    return str(cite.corrected_citation())  # type: ignore[no-untyped-call]


def _reporter_of(citation: FullLawCitation) -> str:
    groups: dict[str, Any] = citation.groups or {}
    reporter = groups.get("reporter")
    if isinstance(reporter, str):
        return reporter.strip()
    return ""


def _law_authority(citation: FullLawCitation) -> AuthorityType:
    return _LAW_REPORTER_TYPES.get(_reporter_of(citation), AuthorityType.OTHER)


def _case_raw_text(citation: FullCaseCitation, corrected: str) -> str:
    """A human-facing case string: ``Plaintiff v. Defendant, <cite> (<court> <year>)``
    when eyecite recovered the case name, else the bare reporter cite."""
    md = citation.metadata
    plaintiff = getattr(md, "plaintiff", None)
    defendant = getattr(md, "defendant", None)
    year = getattr(md, "year", None)
    court = getattr(md, "court", None)
    if plaintiff and defendant:
        paren = " ".join(p for p in (court, str(year) if year else None) if p)
        suffix = f" ({paren})" if paren else ""
        return f"{plaintiff} v. {defendant}, {corrected}{suffix}"
    return corrected


def _mk(raw_text: str, normalized: str, authority: AuthorityType, turn_id: str | None) -> Citation:
    norm = _norm_ws(normalized)
    return Citation(
        citation_id=make_citation_id(norm),
        raw_text=_norm_ws(raw_text),
        normalized=norm,
        authority_type=authority,
        source_turn_id=turn_id,
    )


def extract_citations(text: str, *, source_turn_id: str | None = None) -> list[Citation]:
    """Extract citations from ``text``, deduped by normalized form (first-seen order).

    eyecite runs over ``clean_text(['all_whitespace'])`` (plan D8); a regex pass
    backfills MN court-rule (and, defensively, statute) shapes eyecite does not
    tokenize.
    """
    cleaned = clean_text(text, ["all_whitespace"])
    found: list[Citation] = []
    seen: set[str] = set()

    def _add(citation: Citation) -> None:
        if citation.normalized not in seen:
            seen.add(citation.normalized)
            found.append(citation)

    for cite in get_citations(cleaned):
        if isinstance(cite, FullCaseCitation):
            corrected = _corrected(cite)
            raw = _case_raw_text(cite, corrected)
            _add(_mk(raw, corrected, AuthorityType.CASE, source_turn_id))
        elif isinstance(cite, FullLawCitation):
            corrected = _corrected(cite)
            _add(_mk(corrected, corrected, _law_authority(cite), source_turn_id))

    for match in _RULE_RE.finditer(cleaned):
        _add(_mk(match.group(0), match.group(0), AuthorityType.COURT_RULE, source_turn_id))
    for match in _STATUTE_RE.finditer(cleaned):
        _add(_mk(match.group(0), match.group(0), AuthorityType.STATE_STATUTE, source_turn_id))

    return found
