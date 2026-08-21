"""Citation research and attorney-decision CLI adapters."""

from __future__ import annotations

import json
import os
import pwd
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from mootloop import decisions as decisions_service
from mootloop import orchestrator
from mootloop.citations import verify
from mootloop.citations.check_runner import (
    require_completed_draft_set,
    run_proposition_checks,
)
from mootloop.citations.extract import extract_citations
from mootloop.citations.ledger import ResearchQueue
from mootloop.context import load_run_context
from mootloop.engine.launch import classify_vault_for_queue
from mootloop.engine.queue import Queue, WorkItem
from mootloop.errors import CitationError, DecisionError, MootloopError, VaultBoundaryError
from mootloop.judge_profiles import build_assigned_judge_profile
from mootloop.learn.service import (
    LearningStore,
    import_docx_learning,
    preview_learning_scrub,
    review_learning_proposal,
)
from mootloop.llm import FakeLLMProvider
from mootloop.production_suggestions import (
    ProductionSuggestionStore,
    build_production_suggestions,
    require_production_suggestions_eligible,
    review_production_suggestion,
)
from mootloop.registry import MatterRegistry
from mootloop.vault import load_matter

from . import (
    DecisionActionArg,
    LearningPromotionTierArg,
    ProductionDispositionArg,
    ProductionReviewActionArg,
    _fail,
    _now,
    cite_app,
    decide_app,
    judge_app,
    learn_app,
    production_app,
    research_app,
)


def _print_verify_summary(summary: verify.VerifySummary) -> None:
    typer.echo(f"Citations: {len(summary.outcomes)}  {summary.counts()}")
    for outcome in summary.outcomes:
        verified = outcome.status.value == "verified"
        line = f"  [{outcome.status.value}] {outcome.citation.raw_text}"
        if outcome.source_url:
            line += f"  <{outcome.source_url}>"
        typer.secho(line, fg=typer.colors.GREEN if verified else typer.colors.RED)
    if summary.research_request_ids:
        typer.echo("Research requests opened: " + ", ".join(summary.research_request_ids))
    typer.secho(summary.disclosure, fg=typer.colors.YELLOW)


@cite_app.command("verify")
def cite_verify(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str | None, typer.Option("--run", help="Verify a run's drafts")] = None,
    text_file: Annotated[Path | None, typer.Option("--text", help="Verify a text file")] = None,
) -> None:
    """Extract citations (from a run's drafts or a text file) and verify them."""
    if (run_id is None) == (text_file is None):
        raise _fail(MootloopError("cite verify needs exactly one of --run or --text")) from None
    try:
        if run_id is not None:
            summary = orchestrator.verify_run_citations(vault_path, run_id, _now())
        else:
            assert text_file is not None
            if not text_file.is_file():
                raise MootloopError(f"--text file not found: {text_file}")
            citations = extract_citations(text_file.read_text(encoding="utf-8"))
            summary = verify.verify_all(vault_path, citations, _now())
    except (MootloopError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    _print_verify_summary(summary)


@cite_app.command("check")
def cite_check(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Option("--run", help="Run whose propositions to check")],
    fake: Annotated[bool, typer.Option("--fake", help="Execute with FakeLLMProvider")]
    = False,
) -> None:
    """Queue hosted proposition checks, or execute deterministically with ``--fake``."""
    try:
        load_run_context(vault_path, run_id)
        require_completed_draft_set(vault_path, run_id)
        if fake:
            prepared = run_proposition_checks(
                vault_path,
                run_id,
                FakeLLMProvider(),
                _now(),
            )
            typer.echo(
                f"checked {len(prepared.bundles)} proposition(s); "
                f"research needed for {len(prepared.unresolved)}"
            )
            return
        matter = load_matter(vault_path)
        registry = MatterRegistry()
        hosted_vault = registry.resolve(matter.matter_id)
        if hosted_vault.resolve() != vault_path.resolve():
            raise MootloopError("standalone cite check requires --fake")
        item_id = f"cite:{matter.matter_id}:{run_id}"
        Queue(registry.root).ensure_enqueued(
            WorkItem.create(
                lane="interactive",
                matter_id=matter.matter_id,
                run_id=run_id,
                kind="citation_propositions",
                now=datetime.now(UTC),
                item_id=item_id,
            )
        )
    except (MootloopError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"queued {item_id}")


@research_app.command("list")
def research_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
) -> None:
    """List open citation research requests (citations the free stack cannot verify)."""
    try:
        open_requests = ResearchQueue(vault_path).open_requests()
    except VaultBoundaryError as exc:
        raise _fail(exc) from exc
    if not open_requests:
        typer.echo("No open research requests.")
        return
    for request in open_requests:
        typer.echo(f"{request.request_id}  {request.normalized}  ({request.reason})")
    typer.secho(verify.CITATOR_DISCLOSURE, fg=typer.colors.YELLOW)


@research_app.command("fulfill")
def research_fulfill(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    request_id: Annotated[str, typer.Argument(help="Research request id")],
    file: Annotated[Path, typer.Option("--file", help="Authority markdown to curate")],
    url: Annotated[str | None, typer.Option("--url", help="Source URL for the authority")] = None,
) -> None:
    """Fulfill a research request: curate the authority and mark its citation verified."""
    try:
        record = verify.fulfill_research_request(
            vault_path, request_id, file=file, now=_now(), url=url
        )
    except (CitationError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    typer.echo(f"fulfilled {request_id}: {record.citation_id} verified (curated)")


@judge_app.command("profile")
def judge_profile(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
) -> None:
    """Build locally, or queue the hosted assigned-judge public-opinion profile."""
    try:
        registry = MatterRegistry()
        matter, queue = classify_vault_for_queue(vault_path, registry=registry)
        if queue is not None:
            item_id = f"judge-profile:{matter.matter_id}"
            queue.ensure_enqueued(
                WorkItem.create(
                    lane="interactive",
                    matter_id=matter.matter_id,
                    run_id="judge-profile",
                    kind="judge_profile",
                    now=datetime.now(UTC),
                    item_id=item_id,
                )
            )
            typer.echo(f"queued {item_id}")
            return
        result = build_assigned_judge_profile(vault_path, matter, _now())
    except (MootloopError, VaultBoundaryError) as exc:
        raise _fail(exc) from exc
    if result.profile is None:
        typer.echo(f"research required: {result.warning}")
        return
    status = "calibrated" if result.profile.calibration.calibrated else "uncalibrated"
    typer.echo(
        f"built {result.profile.profile_id}: {status}; "
        f"held-out error={result.profile.calibration.error_rate}"
    )


@production_app.command("generate")
def production_generate(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Option("--run", help="Run whose RFPs to classify")],
) -> None:
    """Build locally, or queue hosted review-only RFP document suggestions."""
    try:
        registry = MatterRegistry()
        matter, queue = classify_vault_for_queue(vault_path, registry=registry)
        require_production_suggestions_eligible(vault_path, run_id)
        if queue is not None:
            item_id = f"production:{matter.matter_id}:{run_id}"
            queue.ensure_enqueued(
                WorkItem.create(
                    lane="interactive",
                    matter_id=matter.matter_id,
                    run_id=run_id,
                    kind="production_suggestions",
                    now=datetime.now(UTC),
                    item_id=item_id,
                )
            )
            typer.echo(f"queued {item_id}")
            return
        result = build_production_suggestions(vault_path, run_id, _now())
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(f"generated {len(result.suggestions)} review-only suggestion(s)")


@production_app.command("list")
def production_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Option("--run", help="Run id")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List folded suggestions without exposing excluded privileged content."""
    try:
        items = ProductionSuggestionStore(vault_path, run_id).list_all()
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in items]))
        return
    if not items:
        typer.echo("No production suggestions.")
        return
    for item in items:
        typer.echo(
            f"{item.suggestion_id}  {item.request_id}  {item.original_name}  "
            f"{item.classification}  [{item.review_status}]"
        )


@production_app.command("show")
def production_show(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Option("--run", help="Run id")],
    suggestion_id: Annotated[str, typer.Argument(help="Suggestion id")],
) -> None:
    item = ProductionSuggestionStore(vault_path, run_id).get(suggestion_id)
    if item is None:
        raise _fail(MootloopError(f"unknown production suggestion {suggestion_id!r}")) from None
    typer.echo(item.model_dump_json(indent=2))


@production_app.command("review")
def production_review(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Option("--run", help="Run id")],
    suggestion_id: Annotated[str, typer.Argument(help="Suggestion id")],
    action: Annotated[
        ProductionReviewActionArg,
        typer.Option("--action", help="accept | reject | production_review"),
    ],
    disposition: Annotated[
        ProductionDispositionArg | None,
        typer.Option("--disposition", help="produce | withhold | defer"),
    ] = None,
    reason: Annotated[str, typer.Option("--reason")] = "",
) -> None:
    """Record a trusted local human review; classification acceptance never produces."""
    try:
        result = review_production_suggestion(
            vault_path,
            run_id,
            suggestion_id,
            action=action.value,
            production_disposition=disposition.value if disposition is not None else None,
            actor=pwd.getpwuid(os.geteuid()).pw_name,
            channel="cli",
            recorded_at=_now(),
            reason=reason,
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(result.model_dump_json())


@learn_app.command("import")
def learn_import(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Option("--run", help="Run whose DOCX was edited")],
    source: Annotated[Path, typer.Argument(help="Attorney-edited DOCX")],
) -> None:
    """Defensively import one local DOCX and create review-only anchored proposals."""
    try:
        result = import_docx_learning(vault_path, run_id, source, imported_at=_now())
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(result.model_dump_json())


@learn_app.command("list")
def learn_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """List the folded learning-review queue across runs in this matter."""
    try:
        store = LearningStore(vault_path)
        items = store.list_all()
        imports = store.list_imports()
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "imports": [record.model_dump(mode="json") for record in imports],
                    "proposals": [proposal.model_dump(mode="json") for proposal in items],
                }
            )
        )
        return
    blocked = [record for record in imports if not record.auto_routable]
    for record in blocked:
        typer.echo(
            f"{record.import_id}  [needs_anchor_review]  "
            + "; ".join(record.blockers)
        )
    if not items and not blocked:
        typer.echo("No learning proposals.")
        return
    for proposal in items:
        tiers = ",".join(proposal.active_tiers) or "none"
        typer.echo(
            f"{proposal.proposal_id}  {proposal.anchor_id}  "
            f"[{proposal.status}]  tiers={tiers}"
        )


@learn_app.command("show")
def learn_show(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    proposal_id: Annotated[str, typer.Argument(help="Learning proposal or import id")],
) -> None:
    store = LearningStore(vault_path)
    item = store.get(proposal_id)
    if item is None:
        bundle = store.load_bundle(proposal_id)
        if bundle is None:
            raise _fail(MootloopError(f"unknown learning item {proposal_id!r}")) from None
        typer.echo(bundle.import_record.model_dump_json(indent=2))
        return
    typer.echo(item.model_dump_json(indent=2))


@learn_app.command("accept")
def learn_accept(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    proposal_id: Annotated[str, typer.Argument(help="Learning proposal id")],
    text: Annotated[str, typer.Option("--text", help="Attorney-reviewed learning text")],
) -> None:
    """Accept a reviewed correction for this matter only."""
    try:
        item = review_learning_proposal(
            vault_path,
            proposal_id,
            action="accept",
            actor=pwd.getpwuid(os.geteuid()).pw_name,
            channel="cli",
            recorded_at=_now(),
            reviewed_text=text,
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(item.model_dump_json())


@learn_app.command("reject")
def learn_reject(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    proposal_id: Annotated[str, typer.Argument(help="Learning proposal id")],
    reason: Annotated[str, typer.Option("--reason")] = "",
) -> None:
    """Reject a proposal; rejected text can never enter a later prompt."""
    try:
        item = review_learning_proposal(
            vault_path,
            proposal_id,
            action="reject",
            actor=pwd.getpwuid(os.geteuid()).pw_name,
            channel="cli",
            recorded_at=_now(),
            reason=reason,
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(item.model_dump_json())


@learn_app.command("scrub")
def learn_scrub(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    proposal_id: Annotated[str, typer.Argument(help="Learning proposal id")],
    text: Annotated[str, typer.Option("--text", help="Proposed shared text")],
) -> None:
    """Fail closed on matter leakage and render the mandatory human promotion diff."""
    try:
        typer.echo(preview_learning_scrub(vault_path, proposal_id, text).model_dump_json())
    except MootloopError as exc:
        raise _fail(exc) from exc


@learn_app.command("promote")
def learn_promote(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    proposal_id: Annotated[str, typer.Argument(help="Accepted learning proposal id")],
    tier: Annotated[LearningPromotionTierArg, typer.Option("--tier")],
    text: Annotated[str, typer.Option("--text", help="Scrubbed shared learning text")],
    confirm_scrub_diff: Annotated[
        bool,
        typer.Option("--confirm-scrub-diff", help="Confirm the rendered public scrub diff"),
    ] = False,
    scrub_diff_sha256: Annotated[
        str,
        typer.Option("--scrub-diff-sha256", help="Exact SHA printed by `learn scrub`"),
    ] = "",
    exclude_matters: Annotated[
        str,
        typer.Option(
            "--exclude-matters",
            help="Comma-separated matter IDs barred by an ethical wall",
        ),
    ] = "",
) -> None:
    """Promote an accepted learning to firm or staged area candidate storage."""
    try:
        item = review_learning_proposal(
            vault_path,
            proposal_id,
            action="promote",
            target_tier=tier.value,
            actor=pwd.getpwuid(os.geteuid()).pw_name,
            channel="cli",
            recorded_at=_now(),
            reviewed_text=text,
            confirm_scrub_diff=confirm_scrub_diff,
            scrub_diff_sha256=scrub_diff_sha256 or None,
            excluded_matter_ids=tuple(
                value.strip() for value in exclude_matters.split(",") if value.strip()
            ),
        )
    except MootloopError as exc:
        raise _fail(exc) from exc
    typer.echo(item.model_dump_json())


@decide_app.command("list")
def decide_list(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    json_output: Annotated[bool, typer.Option("--json", help="Emit decision JSON")] = False,
) -> None:
    """List the run's open attorney-gate decisions."""
    try:
        matter = load_run_context(vault_path, run_id).manifest.matter_config
        open_decisions = decisions_service.DecisionStore(vault_path, run_id).list_open()
    except MootloopError as exc:
        raise _fail(exc) from exc
    if json_output:
        typer.echo(json.dumps([d.model_dump(mode="json") for d in open_decisions]))
        return
    if not open_decisions:
        typer.echo("No open decisions.")
        return
    for decision in open_decisions:
        mode = decisions_service.gate_mode_for(matter, decision.kind)
        typer.echo(f"{decision.decision_id}  [{mode}]  {decision.kind.value}")
        typer.echo(f"  {decision.proposal.summary}")
        typer.echo(f"  recommended: {decision.proposal.recommended}")


@decide_app.command("show")
def decide_show(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    decision_id: Annotated[str, typer.Argument(help="Decision id")],
) -> None:
    """Show a single decision's full proposal (and resolution, if any)."""
    try:
        load_run_context(vault_path, run_id)
        decision = decisions_service.DecisionStore(vault_path, run_id).get(decision_id)
    except MootloopError as exc:
        raise _fail(exc) from exc
    if decision is None:
        raise _fail(DecisionError(f"unknown decision {decision_id!r}")) from None
    typer.echo(decision.model_dump_json(indent=2))


def _resolve_one(
    vault_path: Path,
    run_id: str,
    decision_id: str,
    action: str,
    chosen: str | None,
    note: str,
    by: str,
    source: str,
) -> None:
    decisions_service.resolve(
        vault_path,
        run_id,
        decision_id,
        action,  # type: ignore[arg-type]
        chosen,
        note,
        by,
        source,  # type: ignore[arg-type]
        _now(),
    )


@decide_app.command("resolve")
def decide_resolve(
    vault_path: Annotated[Path, typer.Argument(help="Path to the matter vault")],
    run_id: Annotated[str, typer.Argument(help="Run id")],
    decision_id: Annotated[
        str | None, typer.Argument(help="Decision id (omit with --input)")
    ] = None,
    action: Annotated[
        DecisionActionArg | None, typer.Option("--action", help="approve | modify | deny")
    ] = None,
    choose: Annotated[str | None, typer.Option("--choose", help="Chosen option key")] = None,
    note: Annotated[str, typer.Option("--note", help="Resolution note")] = "",
    input_file: Annotated[
        Path | None, typer.Option("--input", help="JSON list of resolutions (batch)")
    ] = None,
) -> None:
    """Resolve one decision, or a batch via ``--input`` as the local OS principal."""
    try:
        actor = pwd.getpwuid(os.geteuid()).pw_name
        if input_file is not None:
            if not input_file.is_file():
                raise MootloopError(f"--input file not found: {input_file}")
            entries = json.loads(input_file.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                raise MootloopError("--input must be a JSON list of resolutions")
            for entry in entries:
                _resolve_one(
                    vault_path,
                    run_id,
                    entry["decision_id"],
                    entry.get("action", "approve"),
                    entry.get("choose"),
                    entry.get("note", ""),
                    actor,
                    "human",
                )
            typer.echo(f"resolved {len(entries)} decision(s)")
            return
        if decision_id is None or action is None:
            raise MootloopError("single resolve needs <decision-id> and --action")
        _resolve_one(vault_path, run_id, decision_id, action.value, choose, note, actor, "human")
        typer.echo(f"resolved {decision_id}: {action.value}")
    except (MootloopError, KeyError) as exc:
        raise _fail(exc if isinstance(exc, MootloopError) else DecisionError(str(exc))) from exc
