"""Panel-report service (plan Phase 6 / D12): fold the judge panel's `JudgeOutput`
turns into a per-objection survival distribution and persist the derived view.

The pure fold (`fold_objection_results`) pairs each objection in the *judged* draft
with the panel's rulings — one ruling per objection, basis first and position as the
fallback (see `_align_rulings`) — and counts the "would survive a motion to compel"
votes. `build_panel_report` reconstructs the run's
requests, judged drafts, and judge turns, folds every objection, writes the report to
``runs/<run-id>/scores/panels/report.json``, and returns it.

Kept import-light at module top (no orchestrator/stages import) so `stages` can import
the pure fold without a cycle; `build_panel_report` imports the orchestrator lazily.
"""

from __future__ import annotations

from pathlib import Path

from mootloop.models.common import RequestId
from mootloop.models.panels import PanelReport, PanelResult
from mootloop.models.run import JudgeOutput, Objection, ObjectionRuling
from mootloop.vault import atomic_write_text, safe_vault_path

PANEL_REPORT_PATH = ("scores", "panels", "report.json")
DEFAULT_RESTRUCTURE_THRESHOLD = 0.5

_MAX_REASONING_SAMPLES = 3


def _align_rulings(
    objections: list[Objection], judge_output: JudgeOutput
) -> list[ObjectionRuling | None]:
    """One ruling per objection for a single panel member, or None where the judge
    ruled on fewer objections than the draft asserts.

    A ruling is consumed once. Matching the same basis repeatedly is the bug this
    exists to prevent: a draft that asserts two objections on the *same* basis (say
    two relevance objections) would otherwise score both against the judge's FIRST
    relevance ruling — so a second objection the judge said would not survive is
    counted as surviving, and `RestructureStage` never re-enters to fix it.

    Basis match wins over position (judges may list rulings out of order), preferring
    the index-aligned ruling when several share a basis; unmatched objections fall
    back to their positional ruling if it is still unclaimed.
    """
    rulings = judge_output.rulings
    aligned: list[ObjectionRuling | None] = [None] * len(objections)
    claimed: set[int] = set()

    for index, objection in enumerate(objections):
        basis = objection.basis.strip().lower()
        candidates = [
            j
            for j, ruling in enumerate(rulings)
            if j not in claimed and ruling.objection_basis.strip().lower() == basis
        ]
        if not candidates:
            continue
        chosen = index if index in candidates else candidates[0]
        claimed.add(chosen)
        aligned[index] = rulings[chosen]

    for index in range(len(objections)):
        if aligned[index] is None and index < len(rulings) and index not in claimed:
            claimed.add(index)
            aligned[index] = rulings[index]
    return aligned


def fold_objection_results(
    run_id: str,
    request_id: str,
    objections: list[Objection],
    judge_outputs: list[JudgeOutput],
) -> list[PanelResult]:
    """Fold the panel's rulings into one `PanelResult` per objection (pure)."""
    aligned = [_align_rulings(objections, judge_output) for judge_output in judge_outputs]
    results: list[PanelResult] = []
    for index, objection in enumerate(objections):
        survive = 0
        total = 0
        samples: list[str] = []
        for per_judge in aligned:
            ruling = per_judge[index]
            if ruling is None:
                continue
            total += 1
            if ruling.would_objection_survive:
                survive += 1
            if ruling.reasoning.strip() and len(samples) < _MAX_REASONING_SAMPLES:
                samples.append(ruling.reasoning.strip())
        rate = survive / total if total else 0.0
        results.append(
            PanelResult(
                run_id=run_id,
                request_id=RequestId(request_id),
                objection_index=index,
                objection_basis=objection.basis,
                survive_votes=survive,
                total_votes=total,
                survival_rate=rate,
                reasoning_samples=samples,
            )
        )
    return results


def build_panel_report(vault_root: Path | str, run_id: str) -> PanelReport:
    """Fold every request's judge panel into a `PanelReport`, persist it, and return it.

    Written to ``runs/<run-id>/scores/panels/report.json`` via ``safe_vault_path``.
    """
    from mootloop import orchestrator
    from mootloop.journal import load_state
    from mootloop.models.run import DraftOutput

    binding = orchestrator._binding_for(vault_root, run_id)
    state = load_state(vault_root, run_id)
    units = orchestrator.load_request_units(vault_root)
    facts = orchestrator._load_facts(vault_root)

    results: list[PanelResult] = []
    for i in range(len(units)):
        ctx = orchestrator._context_for(
            run_id, state, binding, units, facts, i, orchestrator.DEFAULT_MAX_ATTEMPTS
        )
        draft_record = ctx.judged_draft()
        if draft_record is None:
            continue
        draft = DraftOutput.model_validate(draft_record.output)
        judge_outputs: list[JudgeOutput] = []
        for j in range(1, ctx.config.panels.judges + 1):
            seq = ctx.layout.judge_slot(j)
            if ctx.done(seq):
                judge_outputs.append(JudgeOutput.model_validate(ctx.record(seq).output))
        if not judge_outputs:
            continue
        results.extend(
            fold_objection_results(
                run_id, str(units[i].request_id), draft.objections, judge_outputs
            )
        )

    report = PanelReport(run_id=run_id, results=results)
    path = safe_vault_path(vault_root, "runs", run_id, *PANEL_REPORT_PATH)
    atomic_write_text(path, report.model_dump_json(indent=2) + "\n")
    return report
