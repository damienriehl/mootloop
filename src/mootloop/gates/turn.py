"""Current turn gates adapted to the uniform dependency-ordered runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from mootloop.gates import completeness, degeneracy, fabrication
from mootloop.gates.runtime import (
    DEFAULT_GATE_CATALOG,
    Gate,
    GateRun,
    GateRunner,
    GateScope,
)
from mootloop.models.facts import Fact
from mootloop.models.gates import GateResult
from mootloop.models.rubric import Rubric
from mootloop.models.run import DraftOutput, TurnOutput


@dataclass(frozen=True)
class TurnGateContext:
    """Immutable inputs shared by the gates evaluated when one turn lands."""

    output: TurnOutput
    rubric: Rubric
    request_code: str
    request_text: str
    facts: tuple[Fact, ...]
    corpus_text: str


@dataclass(frozen=True)
class _DegeneracyGate:
    definition = DEFAULT_GATE_CATALOG.definition("degeneracy")

    def applies(self, _context: TurnGateContext) -> bool:
        return True

    def evaluate(self, context: TurnGateContext) -> GateResult:
        return degeneracy.evaluate(context.output)


@dataclass(frozen=True)
class _CompletenessGate:
    definition = DEFAULT_GATE_CATALOG.definition("completeness")

    def applies(self, context: TurnGateContext) -> bool:
        return isinstance(context.output, DraftOutput)

    def evaluate(self, context: TurnGateContext) -> GateResult:
        draft = cast(DraftOutput, context.output)
        return completeness.evaluate(
            draft,
            context.rubric,
            context.request_code,
            context.request_text,
        )


@dataclass(frozen=True)
class _FabricationGate:
    definition = DEFAULT_GATE_CATALOG.definition("fabrication")

    def applies(self, context: TurnGateContext) -> bool:
        return isinstance(context.output, DraftOutput)

    def evaluate(self, context: TurnGateContext) -> GateResult:
        draft = cast(DraftOutput, context.output)
        return fabrication.check(draft, list(context.facts), context.corpus_text)


_TURN_GATES: tuple[Gate[TurnGateContext], ...] = (
    _DegeneracyGate(),
    _CompletenessGate(),
    _FabricationGate(),
)
_TURN_RUNNER = GateRunner(_TURN_GATES)
_LEGACY_GATE_SELECTION = ("degeneracy", "completeness", "rubric")


def normalize_turn_gate_selection(names: tuple[str, ...]) -> tuple[str, ...]:
    """Restore the fabrication gate that pre-U-05 manifests selected implicitly."""
    if names == _LEGACY_GATE_SELECTION:
        return ("degeneracy", "completeness", "fabrication", "rubric")
    return names


def evaluate_turn_gates(names: tuple[str, ...], context: TurnGateContext) -> GateRun:
    """Evaluate configured turn/draft gates after validating the complete graph."""
    ordered = DEFAULT_GATE_CATALOG.order(normalize_turn_gate_selection(names))
    turn_names = tuple(
        definition.name
        for definition in ordered
        if definition.scope in {GateScope.TURN, GateScope.DRAFT}
    )
    return _TURN_RUNNER.run(turn_names, context)
