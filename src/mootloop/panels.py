"""Panel-report service (plan Phase 6 / D12): fold the judge panel's `JudgeOutput`
turns into a per-objection survival distribution and persist the derived view.

The pure fold (`fold_objection_results`) pairs each objection in the *judged* draft
with the panel's rulings (positional, judges rule in objection order) and counts the
"would survive a motion to compel" votes. `build_panel_report` reconstructs the run's
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


def _match_ruling(
    judge_output: JudgeOutput, objection: Objection, index: int
) -> ObjectionRuling | None:
    """The panel member's ruling on ``objection`` — by matching basis, else by the
    objection's position (judges rule on each objection in order)."""
    basis = objection.basis.strip().lower()
    for ruling in judge_output.rulings:
        if ruling.objection_basis.strip().lower() == basis:
            return ruling
    if index < len(judge_output.rulings):
        return judge_output.rulings[index]
    return None


def fold_objection_results(
    run_id: str,
    request_id: str,
    objections: list[Objection],
    judge_outputs: list[JudgeOutput],
) -> list[PanelResult]:
    """Fold the panel's rulings into one `PanelResult` per objection (pure)."""
    results: list[PanelResult] = []
    for index, objection in enumerate(objections):
        survive = 0
        total = 0
        samples: list[str] = []
        for judge_output in judge_outputs:
            ruling = _match_ruling(judge_output, objection, index)
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
