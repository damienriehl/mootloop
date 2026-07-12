"""Thorough coverage for `HeadlessClaudeProvider` (plan FE-1 Unit 3).

Two layers:

- The PURE build seams (`build_allowed_tools`/`build_env`/`build_settings`/`build_argv`)
  are asserted WITHOUT executing a real ``claude`` — the read-only allowlist, the
  minimal env (never a wholesale ``os.environ`` copy), the deny/allow settings, and the
  argv shape (egress wrapper prepended, ``--settings``/``--allowedTools``/JSON output).
- An INTEGRATION layer runs a fake ``claude`` script on PATH: a success case parses to a
  `RawTurnResult`; non-zero exits classify to `SeatLimitError` / `AuthError`. A planted
  injection asserts the sandbox SEAMS (deny rules present, injected sentinel token,
  verbatim redaction) rather than a live jail (jail is deployment config).
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from mootloop import secrets
from mootloop.engine.claude_provider import HeadlessClaudeProvider
from mootloop.errors import AuthError, SeatLimitError
from mootloop.models.run import PersonaName, TurnSpec


def _provider(tmp_path: Path, **kw: object) -> HeadlessClaudeProvider:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    run_dir = tmp_path / "vault" / "runs" / "r1"
    run_dir.mkdir(parents=True, exist_ok=True)
    kw.setdefault("oauth_token_loader", lambda: "sk-ant-oat-TESTTOKEN")
    kw.setdefault("api_key_loader", lambda: "sk-ant-api-TESTKEY")
    return HeadlessClaudeProvider(
        vault_root=vault,
        run_dir=run_dir,
        **kw,  # type: ignore[arg-type]
    )


def _spec() -> TurnSpec:
    return TurnSpec(
        turn_id="t1",
        run_id="r1",
        persona=PersonaName.ASSOCIATE,
        stage="associate_draft",
        output_schema_name="draft",
    )


def _install_fake_claude(bin_dir: Path, body: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write an executable ``claude`` script into ``bin_dir`` and prepend it to PATH."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    script.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


# --- pure seams: allowlist --------------------------------------------------


def test_allowed_tools_are_read_only(tmp_path: Path) -> None:
    tools = _provider(tmp_path).build_allowed_tools()
    assert set(tools) == {"Read", "Glob", "Grep", "LS"}
    for banned in ("Bash", "WebFetch", "WebSearch", "Write", "Edit"):
        assert banned not in tools


# --- pure seams: env --------------------------------------------------------


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


def test_env_does_not_copy_os_environ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A random marker in the parent process must NOT bleed into the subprocess env.
    monkeypatch.setenv("MOOTLOOP_LEAK_MARKER", "should-not-appear")
    env = _provider(tmp_path).build_env()
    assert "MOOTLOOP_LEAK_MARKER" not in env


def test_env_fails_closed_without_token(tmp_path: Path) -> None:
    provider = _provider(tmp_path, oauth_token_loader=lambda: None)
    with pytest.raises(AuthError):
        provider.build_env()


# --- pure seams: settings ---------------------------------------------------


def test_settings_deny_outside_vault_and_secrets(tmp_path: Path) -> None:
    settings = _provider(tmp_path).build_settings()
    deny = settings["permissions"]["deny"]
    assert any(rule.startswith("Read(/**") for rule in deny)  # outside-vault restriction
    assert any(".mootloop/secrets.env" in rule for rule in deny)  # secrets file denied
    assert "Bash" in deny and "WebFetch" in deny and "WebSearch" in deny


def test_settings_allow_scoped_to_vault_realpath(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    allow = provider.build_settings()["permissions"]["allow"]
    vault_real = str(Path(os.path.realpath(provider.vault_root)))
    assert allow == [f"{tool}({vault_real}/**)" for tool in ("Read", "Glob", "Grep", "LS")]


# --- pure seams: argv -------------------------------------------------------


def test_argv_prepends_egress_wrapper_and_has_flags(tmp_path: Path) -> None:
    wrapper = ["bwrap", "--dev-bind", "/", "/", "--unshare-net"]
    provider = _provider(tmp_path, egress_wrapper=wrapper)
    settings_path = tmp_path / "settings.json"
    argv = provider.build_argv("PROMPT", settings_path)
    assert argv[: len(wrapper)] == wrapper  # PREPENDED verbatim
    assert "--settings" in argv and str(settings_path) in argv
    assert "--allowedTools" in argv
    assert "--permission-mode" in argv
    # --output-format json present as an adjacent pair.
    assert argv[argv.index("--output-format") + 1] == "json"
    tools = argv[argv.index("--allowedTools") + 1]
    assert "Bash" not in tools


def test_argv_appends_resume_when_session_present(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    key = provider._session_key(_spec())
    provider._persist_session_id(key, "sess-123")
    settings_path = provider._write_settings()
    argv = provider.build_argv("P", settings_path, session_id=provider._load_session_id(key))
    assert "--resume" in argv and "sess-123" in argv


# --- integration: fake claude on PATH ---------------------------------------

_SUCCESS_BODY = """
import json
print(json.dumps({
    "result": "Response text from the fake claude.",
    "session_id": "sess-fake-1",
    "usage": {"input_tokens": 120, "output_tokens": 40,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    "model": "claude-opus-4-8",
    "total_cost_usd": 0.02,
}))
"""

_SEAT_BODY = """
import sys
sys.stderr.write("Error: rate limit exceeded, try again later\\n")
sys.exit(1)
"""

_AUTH_BODY = """
import sys
sys.stderr.write("authentication_failed: invalid or expired token\\n")
sys.exit(1)
"""


def test_run_turn_parses_fake_claude_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(tmp_path / "bin", _SUCCESS_BODY, monkeypatch)
    result = _provider(tmp_path).run_turn(_spec(), "the prompt")
    assert result.text == "Response text from the fake claude."
    assert result.usage is not None
    assert result.usage.input_tokens == 120
    assert result.usage.output_tokens == 40


def test_run_turn_seat_limit_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_claude(tmp_path / "bin", _SEAT_BODY, monkeypatch)
    with pytest.raises(SeatLimitError):
        _provider(tmp_path).run_turn(_spec(), "the prompt")


def test_run_turn_auth_failure_classified(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_claude(tmp_path / "bin", _AUTH_BODY, monkeypatch)
    with pytest.raises(AuthError):
        _provider(tmp_path).run_turn(_spec(), "the prompt")


# --- planted injection: assert the seams, not a live jail -------------------

# A sentinel that matches NONE of the secret-shape regexes, so redaction can only
# succeed via ``register_secret`` (the build_env seam), not by shape.
_SENTINEL = "oauth-canary-donotleak-000"

_INJECTION_BODY = """
import json, os
token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "<none>")
try:
    outside = open("/etc/hostname").read().strip()
except OSError as exc:
    outside = "blocked:" + str(exc)
print(json.dumps({
    "result": "TOKEN=" + token + " OUTSIDE=" + outside,
    "session_id": "s",
    "usage": {"input_tokens": 1, "output_tokens": 1},
}))
"""


def test_planted_injection_seams_and_token_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(tmp_path / "bin", _INJECTION_BODY, monkeypatch)
    provider = _provider(tmp_path, oauth_token_loader=lambda: _SENTINEL)

    # Seam 1: the deny rules that would block the injection at deploy time are wired in,
    # and the argv carries the --settings path pointing at those rules.
    settings = provider.build_settings()
    deny = settings["permissions"]["deny"]
    assert any(rule.startswith("Read(/**") for rule in deny)  # outside-vault denied
    assert any(".mootloop/secrets.env" in rule for rule in deny)  # secrets denied
    settings_path = provider._write_settings()
    assert str(settings_path) in provider.build_argv("p", settings_path)

    # Seam 2: the subprocess env's token is the injected SENTINEL, never a real token.
    assert provider.build_env()["CLAUDE_CODE_OAUTH_TOKEN"] == _SENTINEL

    # The fake claude echoes its env token back; the subprocess therefore received only
    # the sentinel (proving no real ~/.mootloop token leaked into the env).
    result = provider.run_turn(_spec(), "prompt")
    assert _SENTINEL in result.text

    # Seam 3: register_secret was called in build_env, so redact() scrubs the sentinel
    # verbatim even though it matches none of the secret-shape patterns.
    scrubbed = secrets.redact(result.text)
    assert _SENTINEL not in scrubbed
    assert "***REDACTED***" in scrubbed
