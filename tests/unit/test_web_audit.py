"""Unit tests for the hosted-tier hash-chained access audit (`web/audit.py`).

Complements the invariant test (fold==recompute + field-edit tamper) with a
multi-entry fold, a ``prev_hash``-relink tamper, an entry-reorder tamper, and the
unparseable-line fail-closed case.
"""

from __future__ import annotations

from pathlib import Path

from mootloop.models.audit import GENESIS_PREV_HASH, AccessAuditEntry
from mootloop.web import audit


def _append(vault: Path, i: int) -> None:
    audit.append(
        vault,
        actor="attorney@example.com",
        action="view",
        matter_id="acme-v-widgets",
        resource=f"/api/matters/acme-v-widgets/runs?i={i}",
        ts=f"2026-07-12T00:0{i}:00+00:00",
    )


def _lines(vault: Path) -> list[str]:
    return audit.audit_path(vault).read_text(encoding="utf-8").splitlines()


def _write(vault: Path, lines: list[str]) -> None:
    audit.audit_path(vault).write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_multi_entry_chain_verifies(tmp_path: Path) -> None:
    for i in range(5):
        _append(tmp_path, i)
    entries = _lines(tmp_path)
    assert len(entries) == 5
    assert audit.verify_chain(tmp_path) is True


def test_absent_log_is_intact(tmp_path: Path) -> None:
    # No append has happened; an absent log verifies as intact (nothing to break).
    assert audit.verify_chain(tmp_path) is True


def test_prev_hash_relink_tamper_detected(tmp_path: Path) -> None:
    for i in range(3):
        _append(tmp_path, i)
    lines = _lines(tmp_path)
    # Relink the last entry's prev_hash to genesis (entry_hash left intact) — a forged
    # re-parenting that breaks the chain even though the line still parses cleanly.
    entry = AccessAuditEntry.model_validate_json(lines[2])
    lines[2] = entry.model_copy(update={"prev_hash": GENESIS_PREV_HASH}).model_dump_json()
    assert AccessAuditEntry.model_validate_json(lines[2]).prev_hash == GENESIS_PREV_HASH
    _write(tmp_path, lines)
    assert audit.verify_chain(tmp_path) is False


def test_entry_reorder_tamper_detected(tmp_path: Path) -> None:
    for i in range(3):
        _append(tmp_path, i)
    lines = _lines(tmp_path)
    lines[0], lines[1] = lines[1], lines[0]  # swap the first two entries
    _write(tmp_path, lines)
    assert audit.verify_chain(tmp_path) is False


def test_unparseable_line_fails_closed(tmp_path: Path) -> None:
    _append(tmp_path, 0)
    lines = _lines(tmp_path)
    lines.append("{not valid json")
    _write(tmp_path, lines)
    assert audit.verify_chain(tmp_path) is False
