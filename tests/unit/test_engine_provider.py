"""Light smoke tests for `HeadlessClaudeProvider` build seams (thorough set in Unit 3).

These assert the sandbox is correct WITHOUT executing a real ``claude``: the read-only
tool allowlist, the minimal env (no API key in subscription mode), the deny/allow
settings, and the argv (egress wrapper prepended, settings + allowedTools present).
"""

from __future__ import annotations

from pathlib import Path

from mootloop.engine.claude_provider import HeadlessClaudeProvider
from mootloop.models.run import PersonaName, TurnSpec


def _provider(tmp_path: Path, **kw: object) -> HeadlessClaudeProvider:
    vault = tmp_path / "vault"
    vault.mkdir()
    run_dir = tmp_path / "vault" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    return HeadlessClaudeProvider(
        vault_root=vault,
        run_dir=run_dir,
        oauth_token_loader=lambda: "sk-ant-oat-TESTTOKEN",
        api_key_loader=lambda: "sk-ant-api-TESTKEY",
        **kw,  # type: ignore[arg-type]
    )


def test_allowed_tools_are_read_only(tmp_path: Path) -> None:
    tools = _provider(tmp_path).build_allowed_tools()
    assert set(tools) == {"Read", "Glob", "Grep", "LS"}
    for banned in ("Bash", "WebFetch", "WebSearch", "Write", "Edit"):
        assert banned not in tools


def test_env_subscription_has_token_and_no_api_key(tmp_path: Path) -> None:
    env = _provider(tmp_path).build_env()
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat-TESTTOKEN"
    assert "ANTHROPIC_API_KEY" not in env
    assert env["DISABLE_AUTOUPDATER"] == "1"
    assert env["DISABLE_TELEMETRY"] == "1"
    assert env["CLAUDE_CONFIG_DIR"]


def test_env_api_mode_swaps_credentials(tmp_path: Path) -> None:
    env = _provider(tmp_path, billing_mode="api").build_env()
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-api-TESTKEY"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env


def test_settings_deny_outside_vault_and_secrets(tmp_path: Path) -> None:
    settings = _provider(tmp_path).build_settings()
    deny = settings["permissions"]["deny"]
    assert any(rule.startswith("Read(/**") for rule in deny)  # outside-vault restriction
    assert any(".mootloop/secrets.env" in rule for rule in deny)  # secrets file denied


def test_argv_prepends_egress_wrapper(tmp_path: Path) -> None:
    wrapper = ["bwrap", "--unshare-net"]
    provider = _provider(tmp_path, egress_wrapper=wrapper)
    settings_path = tmp_path / "settings.json"
    argv = provider.build_argv("PROMPT", settings_path)
    assert argv[: len(wrapper)] == wrapper
    assert "--settings" in argv and str(settings_path) in argv
    assert "--allowedTools" in argv
    tools = argv[argv.index("--allowedTools") + 1]
    assert "Bash" not in tools


def test_argv_appends_resume_when_session_present(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    spec = TurnSpec(
        turn_id="t1",
        run_id="r1",
        persona=PersonaName.ASSOCIATE,
        stage="associate_draft",
        output_schema_name="draft",
    )
    key = provider._session_key(spec)
    provider._persist_session_id(key, "sess-123")
    settings_path = provider._write_settings()
    argv = provider.build_argv("P", settings_path, session_id=provider._load_session_id(key))
    assert "--resume" in argv and "sess-123" in argv
