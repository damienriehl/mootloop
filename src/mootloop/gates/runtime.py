"""Uniform gate metadata, dependency validation, and deterministic execution."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar

from mootloop.errors import GateConfigurationError
from mootloop.models.gates import GateResult


class GateScope(StrEnum):
    """The artifact boundary at which a gate is evaluated."""

    TURN = "turn"
    DRAFT = "draft"
    RUN = "run"
    EXPORT = "export"


@dataclass(frozen=True)
class GateDefinition:
    """Stable gate identity plus its dependency and blocking semantics."""

    name: str
    scope: GateScope
    depends_on: tuple[str, ...] = ()
    halt_on_fail: bool = False
    blocks_export: bool = True

    def __post_init__(self) -> None:
        if not self.name:
            raise GateConfigurationError("gate definition name cannot be empty")
        if self.name in self.depends_on:
            raise GateConfigurationError(f"gate {self.name!r} has a dependency cycle")
        if len(set(self.depends_on)) != len(self.depends_on):
            raise GateConfigurationError(f"gate {self.name!r} has duplicate dependencies")


class GateCatalog:
    """Validated gate definitions with stable topological selection ordering."""

    def __init__(self, definitions: Iterable[GateDefinition]) -> None:
        self._definitions: dict[str, GateDefinition] = {}
        for definition in definitions:
            if definition.name in self._definitions:
                raise GateConfigurationError(
                    f"duplicate gate definition {definition.name!r}"
                )
            self._definitions[definition.name] = definition
        for definition in self._definitions.values():
            unknown = [name for name in definition.depends_on if name not in self._definitions]
            if unknown:
                raise GateConfigurationError(
                    f"gate {definition.name!r} depends on unknown gate {unknown[0]!r}"
                )
        self._topological_order(tuple(self._definitions))

    def definition(self, name: str) -> GateDefinition:
        try:
            return self._definitions[name]
        except KeyError as exc:
            raise GateConfigurationError(f"unknown gate {name!r}") from exc

    def order(self, names: Sequence[str]) -> tuple[GateDefinition, ...]:
        """Validate a selected graph and return dependencies before dependents."""
        selected = tuple(names)
        if len(set(selected)) != len(selected):
            raise GateConfigurationError("selected gate list contains duplicates")
        for name in selected:
            definition = self.definition(name)
            for dependency in definition.depends_on:
                if dependency not in selected:
                    raise GateConfigurationError(
                        f"gate {name!r} requires missing dependency {dependency!r}"
                    )
        return tuple(self._definitions[name] for name in self._topological_order(selected))

    def _topological_order(self, names: Sequence[str]) -> tuple[str, ...]:
        selected = tuple(names)
        selected_set = set(selected)
        dependencies = {
            name: set(self._definitions[name].depends_on) & selected_set for name in selected
        }
        ordered: list[str] = []
        while dependencies:
            ready = [name for name in selected if name in dependencies and not dependencies[name]]
            if not ready:
                cycle = ", ".join(name for name in selected if name in dependencies)
                raise GateConfigurationError(f"gate dependency cycle: {cycle}")
            for name in ready:
                ordered.append(name)
                dependencies.pop(name)
                for remaining in dependencies.values():
                    remaining.discard(name)
        return tuple(ordered)


GateContextT = TypeVar("GateContextT", contravariant=True)


class Gate(Protocol[GateContextT]):
    """One gate implementation over a typed, caller-owned immutable context."""

    definition: GateDefinition

    def applies(self, context: GateContextT) -> bool: ...

    def evaluate(self, context: GateContextT) -> GateResult: ...


@dataclass(frozen=True)
class GateRun:
    results: tuple[GateResult, ...]
    halted_by: str | None = None


RunnerContextT = TypeVar("RunnerContextT")


class GateRunner[RunnerContextT]:
    """Run selected gates in dependency order and honor declared halt policy."""

    def __init__(self, gates: Iterable[Gate[RunnerContextT]]) -> None:
        gate_list = tuple(gates)
        self._gates = {gate.definition.name: gate for gate in gate_list}
        if len(self._gates) != len(gate_list):
            raise GateConfigurationError("gate runner contains duplicate implementations")
        self._catalog = GateCatalog(gate.definition for gate in gate_list)

    def run(self, names: Sequence[str], context: RunnerContextT) -> GateRun:
        results: list[GateResult] = []
        for definition in self._catalog.order(names):
            gate = self._gates[definition.name]
            if not gate.applies(context):
                continue
            result = gate.evaluate(context)
            if result.gate != definition.name:
                raise GateConfigurationError(
                    f"gate {definition.name!r} returned result for {result.gate!r}"
                )
            results.append(result)
            if result.status == "fail" and definition.halt_on_fail:
                return GateRun(results=tuple(results), halted_by=definition.name)
        return GateRun(results=tuple(results))


DEFAULT_GATE_CATALOG = GateCatalog(
    (
        GateDefinition(
            name="degeneracy",
            scope=GateScope.TURN,
            halt_on_fail=True,
            blocks_export=False,
        ),
        GateDefinition(
            name="completeness",
            scope=GateScope.DRAFT,
            depends_on=("degeneracy",),
            blocks_export=False,
        ),
        GateDefinition(
            name="fabrication",
            scope=GateScope.DRAFT,
            depends_on=("degeneracy",),
        ),
        GateDefinition(
            name="rubric",
            scope=GateScope.RUN,
            depends_on=("completeness", "fabrication"),
        ),
    )
)
