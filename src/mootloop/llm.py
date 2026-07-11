"""LLM provider seam (plan D1). The orchestrator core never calls a model directly;
it hands a `TurnSpec` + rendered prompt to an `LLMProvider` and gets raw text back.

Three drivers share this one seam: `FakeLLMProvider` (tests), the `mootloop run`
CLI loop (future headless provider), and the Claude Code skill (spawns persona
subagents itself). `RecordingProvider` wraps any provider to persist prompts for
golden tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from mootloop.models.run import (
    SCHEMA_CRITIQUE,
    SCHEMA_DRAFT,
    SCHEMA_JUDGE,
    TurnSpec,
)


@dataclass(frozen=True)
class TokenUsage:
    """Per-turn token accounting (cache-aware, plan D5)."""

    input_tokens: int
    cache_read: int
    cache_write: int
    output_tokens: int
    model: str


@dataclass(frozen=True)
class RawTurnResult:
    """A provider's raw return: unparsed text + optional usage."""

    text: str
    usage: TokenUsage | None


class LLMProvider(Protocol):
    """Persona invocation. Implementations turn a prompt into raw model text."""

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult: ...


# --- cost -------------------------------------------------------------------
# $/1e6 tokens (input, output); pinned model ids per plan D5. Unknown -> free.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-5": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "fake": (0.0, 0.0),
}


def usd_equiv(usage: TokenUsage) -> float:
    """Metered $-equivalent for one call (cache reads 0.1x, writes 1.25x input)."""
    rate_in, rate_out = _PRICES.get(usage.model, (0.0, 0.0))
    return (
        usage.input_tokens * rate_in
        + usage.cache_write * 1.25 * rate_in
        + usage.cache_read * 0.1 * rate_in
        + usage.output_tokens * rate_out
    ) / 1e6


# --- fake provider ----------------------------------------------------------

# A script entry is either a canned output dict or a callable of (spec, prompt).
ScriptEntry = dict[str, Any] | Callable[[TurnSpec, str], dict[str, Any]]
# Keyed by (persona, stage), or by stage alone, or by turn_id.
ScriptKey = tuple[str, str] | str


class FakeLLMProvider:
    """Deterministic provider for tests. Returns schema-valid JSON for every turn.

    Resolution order for a turn: script[turn_id] -> script[(persona, stage)] ->
    script[stage] -> a schema-appropriate default derived from the spec context.
    Records every call in ``calls`` so resume tests can assert no re-execution.
    """

    def __init__(self, script: dict[ScriptKey, ScriptEntry] | None = None) -> None:
        self.script: dict[ScriptKey, ScriptEntry] = script or {}
        self.calls: list[str] = []

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        self.calls.append(spec.turn_id)
        entry = self._resolve(spec)
        output = entry(spec, prompt) if callable(entry) else entry
        usage = TokenUsage(
            input_tokens=len(prompt) // 4,
            cache_read=0,
            cache_write=0,
            output_tokens=len(json.dumps(output)) // 4,
            model="fake",
        )
        return RawTurnResult(text=json.dumps(output), usage=usage)

    def _resolve(self, spec: TurnSpec) -> ScriptEntry:
        for key in (spec.turn_id, (spec.persona.value, spec.stage), spec.stage):
            if key in self.script:
                return self.script[key]
        return _default_output(spec)


def _default_output(spec: TurnSpec) -> dict[str, Any]:
    """A minimal schema-valid, degeneracy-clean output for the spec's schema."""
    ctx = spec.prompt_context
    if spec.output_schema_name == SCHEMA_DRAFT:
        fact_ids = list(ctx.get("fact_ids", []))
        return {
            "response_text": f"Response to {spec.request_id or 'the request'}.",
            "objections": [{"basis": "relevance", "text": "Overbroad as to time."}],
            "candidate_citations": [],
            "fact_ids_used": fact_ids[:1] if fact_ids else [],
            "attorney_gate_items": [] if fact_ids else ["verify factual basis"],
            "self_assessment": "Grounded in the cited fact.",
        }
    if spec.output_schema_name == SCHEMA_CRITIQUE:
        return {
            "verdict": "approve",
            "critiques": [],
            "instructions": [],
            "self_assessment": "The draft is adequate.",
        }
    if spec.output_schema_name == SCHEMA_JUDGE:
        return {
            "rulings": [
                {
                    "objection_basis": "relevance",
                    "would_objection_survive": False,
                    "reasoning": "Discovery relevance is broad.",
                    "persuasion_notes": "Weak objection.",
                }
            ],
            "self_assessment": "Ruled on all objections.",
        }
    raise ValueError(f"no default output for schema {spec.output_schema_name!r}")


# --- recording wrapper ------------------------------------------------------


class RecordingProvider:
    """Wraps a provider, persisting each rendered prompt beside the turn id."""

    def __init__(self, inner: LLMProvider, out_dir: Path | str) -> None:
        self.inner = inner
        self.out_dir = Path(out_dir)

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / f"{spec.turn_id}.prompt.txt").write_text(prompt, encoding="utf-8")
        return self.inner.run_turn(spec, prompt)
