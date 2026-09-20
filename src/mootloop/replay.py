"""Credential-free playback of explicitly prepared responses; never live inference."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from mootloop.context import load_run_context
from mootloop.errors import OrchestratorError
from mootloop.journal import load_state
from mootloop.llm import RawTurnResult
from mootloop.models.replay import PreparedReplay
from mootloop.models.run import TurnSpec


class ReplayProvider:
    def __init__(self, vault_root: Path | str, run_id: str, replay_path: Path) -> None:
        try:
            replay = PreparedReplay.model_validate_json(replay_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise OrchestratorError(f"invalid prepared replay: {exc}") from exc
        context = load_run_context(vault_root, run_id)
        digests = {
            Path(source.locator).stem: source.sha256
            for source in context.manifest.sources
            if source.kind == "document_input"
        }
        discovery_digests = {
            source.locator: source.sha256
            for source in context.manifest.sources
            if source.kind in {"request_set", "fact_repository", "corpus_content"}
        }
        document = context.binding.config.input_family == "document"
        inputs_match = (
            replay.document_input_sha256 == digests and not replay.discovery_input_sha256
            if document
            else replay.discovery_input_sha256 == discovery_digests
            and not replay.document_input_sha256
        )
        if replay.task != context.manifest.task or not inputs_match:
            family = "document" if document else "discovery"
            raise OrchestratorError(f"prepared replay does not match frozen {family} inputs")
        self.vault_root = vault_root
        self.run_id = run_id
        self.responses = {(r.unit_id, r.stage, r.occurrence): r for r in replay.responses}

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        if spec.run_id != self.run_id:
            raise OrchestratorError("prepared replay belongs to a different run")
        state = load_state(self.vault_root, self.run_id)
        occurrence = 1 + sum(
            record.spec.request_id == spec.request_id and record.spec.stage == spec.stage
            for record in state.completed_turns.values()
        )
        key = (str(spec.request_id), spec.stage, occurrence)
        response = self.responses.get(key)
        if response is None or response.output_schema_name != spec.output_schema_name:
            raise OrchestratorError(f"prepared replay has no matching response for {key!r}")
        return RawTurnResult(text=json.dumps(response.output), usage=None)
