"""Uniform gate metadata, dependency ordering, and execution semantics."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from mootloop.errors import GateConfigurationError
from mootloop.gates.runtime import (
    DEFAULT_GATE_CATALOG,
    GateCatalog,
    GateDefinition,
    GateRunner,
    GateScope,
)
from mootloop.gates.turn import normalize_turn_gate_selection
from mootloop.models.gates import GateFail, GateFinding, GatePass, GateResult
from mootloop.tasks import get_binding


@dataclass(frozen=True)
class _RecordingGate:
    definition: GateDefinition
    calls: list[str]
    result: GateResult

    def applies(self, _context: object) -> bool:
        return True

    def evaluate(self, _context: object) -> GateResult:
        self.calls.append(self.definition.name)
        return self.result


def _definition(
    name: str,
    *,
    depends_on: tuple[str, ...] = (),
    halt_on_fail: bool = False,
) -> GateDefinition:
    return GateDefinition(
        name=name,
        scope=GateScope.TURN,
        depends_on=depends_on,
        halt_on_fail=halt_on_fail,
        blocks_export=False,
    )


def test_catalog_orders_dependencies_before_dependents_stably() -> None:
    catalog = GateCatalog(
        (
            _definition("final", depends_on=("ground", "presence")),
            _definition("ground", depends_on=("parse",)),
            _definition("presence", depends_on=("parse",)),
            _definition("parse"),
        )
    )

    ordered = catalog.order(("final", "presence", "parse", "ground"))

    assert [gate.name for gate in ordered] == ["parse", "presence", "ground", "final"]


def test_catalog_rejects_duplicate_unknown_missing_and_cyclic_definitions() -> None:
    with pytest.raises(GateConfigurationError, match="name cannot be empty"):
        _definition("")
    with pytest.raises(GateConfigurationError, match="dependency cycle"):
        _definition("one", depends_on=("one",))
    with pytest.raises(GateConfigurationError, match="duplicate dependencies"):
        _definition("one", depends_on=("two", "two"))

    with pytest.raises(GateConfigurationError, match="duplicate gate definition"):
        GateCatalog((_definition("one"), _definition("one")))

    with pytest.raises(GateConfigurationError, match="depends on unknown gate 'ghost'"):
        GateCatalog((_definition("one", depends_on=("ghost",)),))

    catalog = GateCatalog((_definition("one"), _definition("two", depends_on=("one",))))
    with pytest.raises(GateConfigurationError, match="unknown gate"):
        catalog.order(("ghost",))
    with pytest.raises(GateConfigurationError, match="requires missing dependency 'one'"):
        catalog.order(("two",))

    with pytest.raises(GateConfigurationError, match="dependency cycle"):
        GateCatalog(
            (
                _definition("one", depends_on=("two",)),
                _definition("two", depends_on=("one",)),
            )
        )


def test_runner_stops_after_a_halting_failure() -> None:
    calls: list[str] = []
    parse = _RecordingGate(
        _definition("parse", halt_on_fail=True),
        calls,
        GateFail(gate="parse", findings=[GateFinding(code="bad", message="bad")]),
    )
    later = _RecordingGate(
        _definition("later", depends_on=("parse",)),
        calls,
        GatePass(gate="later"),
    )

    run = GateRunner((later, parse)).run(("later", "parse"), object())

    assert calls == ["parse"]
    assert [result.gate for result in run.results] == ["parse"]
    assert run.halted_by == "parse"


def test_runner_rejects_duplicate_implementations_and_mismatched_results() -> None:
    calls: list[str] = []
    definition = _definition("parse")
    gate = _RecordingGate(definition, calls, GatePass(gate="parse"))
    with pytest.raises(GateConfigurationError, match="duplicate implementations"):
        GateRunner((gate, gate))

    mismatched = _RecordingGate(definition, calls, GatePass(gate="other"))
    with pytest.raises(GateConfigurationError, match="returned result for 'other'"):
        GateRunner((mismatched,)).run(("parse",), object())


def test_runner_skips_inapplicable_gates_and_keeps_nonhalting_failures() -> None:
    @dataclass(frozen=True)
    class _SkippedGate:
        definition: GateDefinition

        def applies(self, _context: object) -> bool:
            return False

        def evaluate(self, _context: object) -> GateResult:
            raise AssertionError("an inapplicable gate must not be evaluated")

    calls: list[str] = []
    failed = _RecordingGate(
        _definition("failed"),
        calls,
        GateFail(gate="failed", findings=[GateFinding(code="bad", message="bad")]),
    )
    passed = _RecordingGate(
        _definition("passed", depends_on=("failed",)),
        calls,
        GatePass(gate="passed"),
    )
    skipped = _SkippedGate(_definition("skipped", depends_on=("passed",)))

    run = GateRunner((skipped, passed, failed)).run(
        ("skipped", "passed", "failed"), object()
    )

    assert calls == ["failed", "passed"]
    assert [result.gate for result in run.results] == ["failed", "passed"]
    assert run.halted_by is None


def test_task_adapter_declares_every_current_turn_gate_in_dependency_order() -> None:
    binding = get_binding("discovery-responses")

    ordered = DEFAULT_GATE_CATALOG.order(binding.config.gates)

    assert [gate.name for gate in ordered] == [
        "degeneracy",
        "completeness",
        "fabrication",
        "rubric",
    ]


def test_exact_legacy_selection_restores_the_previously_implicit_fabrication_gate() -> None:
    assert normalize_turn_gate_selection(("degeneracy", "completeness", "rubric")) == (
        "degeneracy",
        "completeness",
        "fabrication",
        "rubric",
    )
