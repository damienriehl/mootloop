from pathlib import Path

import pytest

from mootloop.context_sources import ContextContributionStore
from mootloop.errors import LearningImportError
from mootloop.learn.service import LearningStore, import_docx_learning, review_learning_proposal
from mootloop.models.context import ContextContribution
from tests.unit.test_learning_service import NOW, _edited_docx, _vault


def test_accept_retry_repairs_missing_contribution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, run_id = _vault(tmp_path)
    edited = _edited_docx(tmp_path, "State when the inspection occurred. RESPONSE: May.")
    imported = import_docx_learning(vault, run_id, edited, imported_at=NOW)
    proposal_id = imported.proposals[0].proposal_id
    original = ContextContributionStore.put

    def fail(self: ContextContributionStore, value: ContextContribution) -> None:
        raise OSError("interrupted contribution publication")

    monkeypatch.setattr(ContextContributionStore, "put", fail)
    with pytest.raises(OSError, match="interrupted"):
        review_learning_proposal(
            vault,
            proposal_id,
            action="accept",
            actor="Reviewer",
            channel="cli",
            recorded_at=NOW,
            reviewed_text="Reviewed learning",
        )
    assert len(LearningStore(vault).review_events()) == 1
    monkeypatch.setattr(ContextContributionStore, "put", original)
    for _ in range(2):
        result = review_learning_proposal(
            vault,
            proposal_id,
            action="accept",
            actor="Reviewer",
            channel="cli",
            recorded_at="2026-08-22T00:00:00+00:00",
            reviewed_text="Reviewed learning",
        )
        assert result.status == "accepted"
    assert len(LearningStore(vault).review_events()) == 1
    assert ContextContributionStore(vault).list_all()[0].text == "Reviewed learning"
    with pytest.raises(LearningImportError, match="final review"):
        review_learning_proposal(
            vault,
            proposal_id,
            action="accept",
            actor="Reviewer",
            channel="cli",
            recorded_at=NOW,
            reviewed_text="Different text",
        )
