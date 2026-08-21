"""Deterministic proposal construction before hard-human tier routing."""

from __future__ import annotations

from mootloop.learn.diff import critic_markup, sha256_text
from mootloop.models.common import (
    LearningImportId,
    LearningProposalId,
    MatterId,
    RunId,
    canonical_json_sha256,
)
from mootloop.models.learnings import LearningProposal, RecoveredAnchor


def propose_anchored_learning(
    recovered: RecoveredAnchor,
    *,
    baseline_text: str,
    import_id: LearningImportId,
    matter_id: MatterId,
    run_id: RunId,
    task: str,
    created_at: str,
) -> LearningProposal | None:
    if baseline_text == recovered.current_text:
        return None
    markup, changes = critic_markup(baseline_text, recovered.current_text)
    digest = canonical_json_sha256(
        {
            "import": str(import_id),
            "anchor": recovered.anchor_id,
            "before": sha256_text(baseline_text),
            "after": sha256_text(recovered.current_text),
        }
    )
    return LearningProposal(
        proposal_id=LearningProposalId(f"learning-{digest[:16]}"),
        import_id=import_id,
        source_matter_id=matter_id,
        run_id=run_id,
        task=task,
        anchor_id=recovered.anchor_id,
        baseline_text=baseline_text,
        edited_text=recovered.current_text,
        baseline_sha256=sha256_text(baseline_text),
        edited_sha256=sha256_text(recovered.current_text),
        critic_markup=markup,
        word_changes=changes,
        proposed_text=(
            f"For {recovered.anchor_id}, prefer the attorney-reviewed edited language: "
            f"{recovered.current_text}"
        ),
        created_at=created_at,
    )
