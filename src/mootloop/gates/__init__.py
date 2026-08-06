"""Deterministic and LLM gates. Each gate maps a validated artifact to a
`GateResult` (never raises for a mere failure — failure is an artifact state).

`has_hedge` lives here because two gates condemn the same boilerplate and must
agree on what counts as it.
"""

from __future__ import annotations

import re
import unicodedata

# The condemned "subject to and without waiving" hedge (Liguria Foods; plan D7).
# Matched on NFKC-normalized text with flexible interior punctuation/whitespace: the
# comma'd form ("Subject to, and without waiving,") is the MORE common drafting, and a
# non-breaking space between the words is what a paste out of Word produces. A literal
# substring test passed all of those, and `export/master.py` relies on this gate to keep
# the phrase out of the served document.
_HEDGE_RE = re.compile(
    r"subject\s*,?\s*to\s*,?\s*and\s*,?\s*without\s*,?\s*waiv(?:ing|er|ed)",
    re.IGNORECASE,
)

HEDGE_DESCRIPTION = '"subject to and without waiving" (Liguria Foods)'


def has_hedge(*texts: str) -> bool:
    """True when any of ``texts`` carries the condemned reserve-while-answering hedge."""
    return any(_HEDGE_RE.search(unicodedata.normalize("NFKC", text)) for text in texts)
