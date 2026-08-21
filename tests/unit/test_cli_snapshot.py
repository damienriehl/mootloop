"""Behavioral snapshot for the agent-facing Typer command surface."""

from __future__ import annotations

from pathlib import Path

from typer.models import CommandInfo
from typer.testing import CliRunner

from mootloop.cli import app

_COMMAND_TREE = {
    "api": ["export-openapi"],
    "cite": ["verify"],
    "corpus": ["actions", "convert", "tag"],
    "decide": ["list", "resolve", "show"],
    "driver": ["run-once", "serve", "start-matter-worker"],
    "export": ["build", "link"],
    "facts": ["add", "interview", "list", "propose", "review"],
    "matters": ["list"],
    "requests": ["parse"],
    "research": ["fulfill", "list"],
    "run": [
        "blockers",
        "continue",
        "drive",
        "estimate",
        "gates",
        "panels",
        "pause",
        "plan-next",
        "prompt",
        "raise-cap",
        "record-turn",
        "reopen",
        "resume",
        "start",
        "status",
    ],
    "tasks": ["freeform", "list", "lock"],
    "web": ["bake"],
}
_ROOT_COMMANDS = [
    "attest",
    "attest-status",
    "backup",
    "close",
    "ingest",
    "init",
    "restore",
    "validate",
]


def _command_name(command: CommandInfo) -> str:
    name = command.name
    if name is not None:
        return str(name)
    callback = command.callback
    assert callback is not None
    return str(callback.__name__).replace("_", "-")


def test_command_tree_snapshot() -> None:
    assert sorted(_command_name(command) for command in app.registered_commands) == _ROOT_COMMANDS
    assert {
        group.name: sorted(
            _command_name(command) for command in group.typer_instance.registered_commands
        )
        for group in app.registered_groups
    } == _COMMAND_TREE


def test_every_group_help_remains_reachable() -> None:
    runner = CliRunner()
    root = runner.invoke(app, ["--help"])
    assert root.exit_code == 0
    assert "MootLoop — agentic law firm simulator." in root.stdout
    for group in _COMMAND_TREE:
        result = runner.invoke(app, [group, "--help"])
        assert result.exit_code == 0, (group, result.stdout, result.exception)


def test_large_command_families_are_registered_from_focused_modules() -> None:
    groups = {group.name: group.typer_instance for group in app.registered_groups}
    expected_modules = {
        "run": "mootloop.cli.run",
        "cite": "mootloop.cli.review",
        "research": "mootloop.cli.review",
        "decide": "mootloop.cli.review",
        "driver": "mootloop.cli.operations",
        "api": "mootloop.cli.operations",
    }
    for group_name, module_name in expected_modules.items():
        callbacks = [command.callback for command in groups[group_name].registered_commands]
        assert callbacks
        assert all(
            callback is not None and callback.__module__ == module_name
            for callback in callbacks
        )


def test_cli_package_has_no_monolithic_thousand_line_module() -> None:
    cli_root = Path(__file__).parents[2] / "src/mootloop/cli"
    sizes = {
        path.name: len(path.read_text(encoding="utf-8").splitlines())
        for path in cli_root.glob("*.py")
    }
    assert sizes
    assert max(sizes.values()) < 1_000, sizes
