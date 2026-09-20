"""Offline, credential-free preparation through the real task planner and journal."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from mootloop.context import load_run_context
from mootloop.demo_inputs import import_bundle, validate_bundle
from mootloop.gate_ledger import build_ledger
from mootloop.llm import RawTurnResult
from mootloop.models.demo import (
    BundleFile,
    DemoGateState,
    DemoProvenance,
    DemoSnapshot,
    DemoStage,
    DemoStrategy,
    LocalInputBundle,
)
from mootloop.models.demo_preparation import AuthoredStrategy, DemoPreparation
from mootloop.models.document_task import DocumentTaskInput
from mootloop.models.replay import PreparedReplay, ReplayResponse
from mootloop.models.run import TurnRecord, TurnSpec
from mootloop.orchestrator import operative_draft_records, run_with_provider, start_run
from mootloop.replay import ReplayProvider
from mootloop.vault import enclosing_git_repo
from mootloop.web.catalog import PublicationError

NOW = "2026-09-20T00:00:00+00:00"


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class AuthoredProvider:
    """Use only an explicitly authored unit; never synthesize missing legal output."""

    def __init__(self, strategy: AuthoredStrategy):
        self.strategy = strategy

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        unit = self.strategy.outputs.get(str(spec.request_id))
        if unit is None:
            raise PublicationError("no authored output for unit")
        output: BaseModel
        schema = spec.output_schema_name
        if schema == "draft":
            output = unit.initial if spec.stage == "associate_draft" else unit.revised
        elif schema == "critique":
            output = unit.critique
        elif schema == "rubric_score":
            draft = spec.prompt_context.get("draft") or {}
            rubric = (
                unit.initial_rubric
                if draft.get("response_text") == unit.initial.response_text
                else unit.rubric
            )
            output = rubric
            actual = {item["id"] for item in spec.prompt_context.get("criteria", [])}
            if {score.criterion_id for score in rubric.scores} != actual:
                raise PublicationError("authored rubric does not match locked criteria")
        elif schema == "narrative_assessment":
            output = unit.assessment
        elif schema == "judge" and unit.judge is not None:
            output = unit.judge
        else:
            raise PublicationError(f"no authored response for schema {schema}")
        return RawTurnResult(text=output.model_dump_json(), usage=None)


def _text(record: TurnRecord) -> str:
    output = record.output
    if "response_text" in output:
        return str(output["response_text"])
    if record.spec.output_schema_name == "critique":
        return "\n\n".join(
            [
                "Prepared reviewer disposition: " + str(output["verdict"]),
                *("• " + str(item) for item in output["critiques"]),
                "Revision instructions:",
                *("• " + str(item) for item in output["instructions"]),
                str(output["self_assessment"]),
            ]
        )
    if record.spec.output_schema_name == "narrative_assessment":
        return "\n\n".join(
            [
                "Prepared assessment: " + str(output["disposition"]),
                *map(str, output["reasons"]),
                "Limits:",
                *map(str, output["limitations"]),
            ]
        )
    return json.dumps(output, indent=2, ensure_ascii=False)


def _stage(
    kind: Literal["initial", "critique", "revised", "assessment"], records: list[TurnRecord]
) -> DemoStage:
    if not records:
        raise PublicationError(f"missing recorded {kind} stage")
    return DemoStage(
        kind=kind,
        title=kind.title(),
        text="\n\n---\n\n".join(
            f"### {record.spec.request_id} · {record.spec.persona.value}\n\n{_text(record)}"
            for record in records
        ),
        turn_ids=tuple(str(record.spec.turn_id) for record in records),
    )


def local_instructions(preparation: DemoPreparation) -> str:
    demo_id = preparation.descriptor.demo_id
    task = preparation.descriptor.task
    lines = [
        "Install Python 3.12 and uv; clone "
        "https://github.com/damienriehl/mootloop and run uv sync.",
        "Save this demo’s inputs.json outside the checkout. Keep vaults and "
        "replay files outside the checkout.",
        f"uv run mootloop web import-demo /path/to/inputs.json "
        f"/path/to/external-vault --matter-id 2026-09-20-{demo_id}",
        "These are prepared scripts, not fresh model inference. No provider "
        "key is needed for replay.",
    ]
    for strategy in preparation.strategies:
        run_id = strategy.strategy_id
        selection = "".join(" --document-input " + key for key in strategy.input_ids)
        lines += [
            f"uv run mootloop web replay-script /path/to/inputs.json {run_id} "
            f"/path/to/{run_id}.json",
            f"uv run mootloop run start /path/to/external-vault --task {task} "
            f"--run-id {run_id}{selection} --mode autonomous",
            f"uv run mootloop run drive /path/to/external-vault {run_id} --replay "
            f"/path/to/{run_id}.json",
            f"uv run mootloop run status /path/to/external-vault {run_id}",
        ]
    lines += [
        "For your own review, use run plan-next, run prompt, and run "
        "record-turn with schema-valid outputs instead of replay; run --help "
        "documents each command.",
        "Document master.md files are draft review copies. Unresolved "
        "citation, decision and attorney gates remain visible. No scripted result "
        "is attorney approval.",
    ]
    return "\n\n".join(lines)


def _gate_status(value: str) -> Literal["pass", "fail", "not_evaluated"]:
    if value == "pass":
        return "pass"
    if value == "fail":
        return "fail"
    return "not_evaluated"


def prepare_demo(
    fixture: Path, work: Path, output: Path, *, revision: str, software_revision: str
) -> DemoSnapshot:
    preparation = DemoPreparation.model_validate_json((fixture / "preparation.json").read_bytes())
    if enclosing_git_repo(work) or enclosing_git_repo(output):
        raise PublicationError("preparation runs and snapshots must be outside Git checkouts")
    if preparation.descriptor.collection == "public-record" and enclosing_git_repo(fixture):
        raise PublicationError("real case inputs must be outside Git checkouts")
    if output.exists() and any(output.iterdir()):
        raise PublicationError("snapshot preparation requires an empty output directory")
    files = []
    for path in sorted(fixture.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise PublicationError("fixture must contain only regular allowlisted files")
        if path.name == "preparation.json":
            continue
        raw = path.read_bytes()
        files.append(BundleFile(name=path.name, text=raw.decode("utf-8"), sha256=_digest(raw)))
    instructions = local_instructions(preparation)
    bundle = LocalInputBundle(
        demo_id=preparation.descriptor.demo_id,
        revision=revision,
        task=preparation.descriptor.task,
        software_revision=software_revision,
        instructions=instructions,
        files=tuple(files),
    )
    validate_bundle(bundle)
    # Bind authored strategy identity to its exact documents before any run exists.
    inputs = {file.name: file for file in files}
    for strategy in preparation.strategies:
        for key in strategy.input_ids:
            document = DocumentTaskInput.model_validate_json(
                inputs["document-" + key + ".json"].text
            )
            if document.strategy_id != strategy.strategy_id:
                raise PublicationError("authored strategy does not match document strategy")
            if {unit.unit_id for unit in document.units} - strategy.outputs.keys():
                raise PublicationError("missing authored document unit")
    strategies = []
    replay_files = []
    for strategy in preparation.strategies:
        base = work / strategy.strategy_id
        vault = import_bundle(
            bundle,
            base / "vault",
            matter_id="2026-09-20-" + preparation.descriptor.demo_id + "-" + strategy.strategy_id,
            registry_path=work / "canaries.json",
        )
        run_id = "author-" + strategy.strategy_id
        start_run(
            vault,
            bundle.task,
            NOW,
            run_id=run_id,
            document_input_refs=list(strategy.input_ids) if strategy.input_ids else None,
        )
        state = run_with_provider(vault, run_id, AuthoredProvider(strategy), NOW)
        if state.status != "finished" or state.discarded:
            raise PublicationError(f"authored workflow failed: {state.status}; {state.discarded}")
        context = load_run_context(vault, run_id)
        counts: dict[tuple[str, str], int] = {}
        responses = []
        for record in state.completed_turns.values():
            turn_key = (str(record.spec.request_id), record.spec.stage)
            counts[turn_key] = counts.get(turn_key, 0) + 1
            responses.append(
                ReplayResponse(
                    unit_id=turn_key[0],
                    stage=turn_key[1],
                    occurrence=counts[turn_key],
                    output_schema_name=record.spec.output_schema_name,
                    output=record.output,
                )
            )
        replay = PreparedReplay(
            task=bundle.task,
            document_input_sha256={
                Path(s.locator).stem: s.sha256
                for s in context.manifest.sources
                if s.kind == "document_input"
            },
            discovery_input_sha256={
                s.locator: s.sha256
                for s in context.manifest.sources
                if s.kind in {"request_set", "fact_repository", "corpus_content"}
            }
            if bundle.task == "discovery-responses"
            else {},
            responses=responses,
        )
        replay_text = replay.model_dump_json(indent=2) + "\n"
        replay_path = base / ("replay-" + strategy.strategy_id + ".json")
        replay_path.write_text(replay_text)
        replay_files.append(
            BundleFile(
                name=replay_path.name, text=replay_text, sha256=_digest(replay_text.encode())
            )
        )
        # Public trails come from replay with a fresh run identity, not authoring input text.
        replay_id = "replay-" + strategy.strategy_id
        start_run(
            vault,
            bundle.task,
            NOW,
            run_id=replay_id,
            document_input_refs=list(strategy.input_ids) if strategy.input_ids else None,
        )
        state = run_with_provider(
            vault, replay_id, ReplayProvider(vault, replay_id, replay_path), NOW
        )
        if state.status != "finished" or state.discarded:
            raise PublicationError("prepared replay failed to reproduce complete stages")
        records = list(state.completed_turns.values())
        operative = [
            record
            for _, record in operative_draft_records(
                replay_id, load_run_context(vault, replay_id), state
            )
            if record
        ]
        ledger = build_ledger(vault, replay_id)
        status: dict[str, Literal["pass", "fail", "not_evaluated"]] = {}
        for key, value in ledger.overall.items():
            status[key] = _gate_status(value)
        for unit_id, gates in ledger.gates.items():
            for key, value in gates.items():
                status[unit_id + ":" + key] = _gate_status(value)
        strategies.append(
            DemoStrategy(
                strategy_id=strategy.strategy_id,
                title=strategy.title,
                run_id=replay_id,
                input_summary=strategy.input_summary,
                assumptions=strategy.assumptions,
                source_ids=strategy.source_ids,
                stages=(
                    _stage("initial", [r for r in records if r.spec.stage == "associate_draft"]),
                    _stage(
                        "critique", [r for r in records if r.spec.output_schema_name == "critique"]
                    ),
                    _stage("revised", operative),
                    _stage(
                        "assessment",
                        [
                            r
                            for r in records
                            if r.spec.output_schema_name in {"narrative_assessment", "judge"}
                        ],
                    ),
                ),
                gate_state=DemoGateState(
                    run_status=state.status,
                    export_ready=ledger.export_ready,
                    blockers=tuple(ledger.blockers),
                    results=status,
                ),
            )
        )
    bundle = LocalInputBundle.model_validate(
        bundle.model_dump() | {"files": (*files, *replay_files)}
    )
    validate_bundle(bundle)
    bundle_raw = bundle.model_dump_json(indent=2) + "\n"
    snapshot = DemoSnapshot(
        descriptor=preparation.descriptor,
        revision=revision,
        provenance=DemoProvenance(
            preparation="scripted-replay",
            authorship="Agent-authored, scenario-specific prepared scripts replayed through "
            "the MootLoop task planner.",
            editorial_changes="Drafts, critiques and assessments were written for this "
            "demonstration. The displayed stages are extracted from replayed journal "
            "records; no live model provider was called.",
            provider_calls=0,
            limitations=preparation.limitations,
        ),
        strategies=tuple(strategies),
        sources=preparation.sources,
        claims=preparation.claims,
        comparison=preparation.comparison,
        actual_outcome=preparation.actual_outcome,
        outcome_source_ids=preparation.outcome_source_ids,
        local_instructions=instructions,
        bundle_sha256=_digest(bundle_raw.encode()),
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "inputs.json").write_text(bundle_raw)
    (output / "snapshot.json").write_text(snapshot.model_dump_json(indent=2) + "\n")
    return snapshot
