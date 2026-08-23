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

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from mootloop import secrets
from mootloop.engine.claude_provider import (
    ENGINE_CONFIG_ENV,
    HeadlessClaudeProvider,
    _unfence,
)
from mootloop.engine.egress_exec import validate_isolated_command
from mootloop.engine.isolation import ProxyIdentity, hosted_wrapper
from mootloop.errors import (
    AuthError,
    EgressError,
    OutboundPrivacyError,
    SeatLimitError,
    TurnError,
    VaultBoundaryError,
)
from mootloop.models.run import PersonaName, TurnSpec


def _provider(tmp_path: Path, **kw: object) -> HeadlessClaudeProvider:
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    run_dir = Path(kw.pop("run_dir", tmp_path / "vault" / "runs" / "r1"))  # type: ignore[arg-type]
    run_dir.mkdir(parents=True, exist_ok=True)
    kw.setdefault("oauth_token_loader", lambda: "sk-ant-oat-TESTTOKEN")
    kw.setdefault("api_key_loader", lambda: "sk-ant-api-TESTKEY")
    if kw.get("runtime_mode") == "hosted":
        kw.setdefault("matter_id", "2026-08-21-test-matter")
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


def test_persona_turns_receive_no_filesystem_tools(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    assert provider.build_allowed_tools() == []
    permissions = provider.build_settings()["permissions"]
    assert permissions["allow"] == []
    assert "Read(//**)" in permissions["deny"]


def test_diagnostic_read_tools_are_read_only(tmp_path: Path) -> None:
    tools = _provider(tmp_path).build_allowed_tools(allow_vault_reads=True)
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
    outsider = tmp_path / "outsider"
    outsider.mkdir()
    settings = _provider(tmp_path).build_settings(allow_vault_reads=True)
    deny = settings["permissions"]["deny"]
    assert f"Read(/{outsider})" in deny  # outside-vault restriction
    assert f"Read(/{outsider}/**)" in deny
    assert any(".mootloop/secrets.env" in rule for rule in deny)  # secrets file denied
    assert "Bash" in deny and "WebFetch" in deny and "WebSearch" in deny


def test_every_path_rule_is_anchored_at_the_filesystem_root(tmp_path: Path) -> None:
    """REGRESSION. Claude Code resolves a rule path with ONE leading slash against the
    directory holding the settings file; only `//` means absolute. So
    `Read(/home/you/.mootloop/secrets.env)` denied `<run_dir>/home/you/…` — a path that
    does not exist — and the secrets file was never actually protected by a rule of its
    own. Proved against `claude` 2.1.222: single-slash deny returns the file, double-slash
    deny returns the permission-settings refusal."""
    settings = _provider(tmp_path).build_settings()
    for section in ("deny", "allow"):
        for rule in settings["permissions"][section]:
            if "(" not in rule:
                continue  # bare tool name, e.g. "Bash"
            spec = rule[rule.index("(") + 1 : -1]
            if not spec.startswith("/"):
                continue  # an intentionally relative pattern, if any
            assert spec.startswith("//"), f"{section} rule {rule!r} is settings-relative"


def test_settings_never_blanket_deny_reads(tmp_path: Path) -> None:
    """REGRESSION. `build_settings` denied `Read(/**)` and then re-allowed
    `Read(<vault>/**)`. Claude Code evaluates deny before allow with no re-open, so the
    allow was dead and the persona could not open a single file — and because `Read(/**)`
    gates path access rather than the tool named `Read`, `Glob` and `Grep` over the vault
    were denied too. Every headless turn ran blind, drafting from prompt text alone.

    Proved against `claude` 2.1.222: with this rule a vault Read returns
    "File is in a directory that is denied by your permission settings"; delete the one
    string and the identical prompt returns the file's contents."""
    provider = _provider(tmp_path)
    settings = provider.build_settings(allow_vault_reads=True)
    deny = settings["permissions"]["deny"]
    vault_real = Path(os.path.realpath(provider.vault_root))

    assert "Read(/**)" not in deny and "Read(//**)" not in deny
    # More generally: no read-deny rule may cover the vault or any ancestor of it, or the
    # allow list below it is inert.
    protected = {f"/{vault_real}", *(f"/{p}" for p in vault_real.parents)}
    for rule in deny:
        if not rule.startswith("Read("):
            continue
        target = rule[len("Read(") : -1].removesuffix("/**")
        assert target not in protected, f"deny rule {rule!r} swallows the vault"


def test_settings_deny_reaches_the_whole_ancestor_chain(tmp_path: Path) -> None:
    """The complement construction must cover siblings at EVERY level, not just the
    vault's own directory — otherwise `/etc`, `~/.ssh` and friends stay readable."""
    nested = tmp_path / "a" / "b" / "vault"
    nested.mkdir(parents=True)
    (tmp_path / "a" / "cousin.txt").write_text("x", encoding="utf-8")
    (tmp_path / "aunt").mkdir()
    provider = HeadlessClaudeProvider(
        vault_root=nested,
        run_dir=nested / "runs" / "r1",
        oauth_token_loader=lambda: "sk-ant-oat-TESTTOKEN",
    )
    deny = provider.build_settings(allow_vault_reads=True)["permissions"]["deny"]
    assert f"Read(/{tmp_path / 'a' / 'cousin.txt'})" in deny  # one level up
    assert f"Read(/{tmp_path / 'aunt'}/**)" in deny  # two levels up
    assert "Read(//etc/**)" in deny  # all the way to the root


def test_settings_allow_scoped_to_vault_realpath(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    allow = provider.build_settings(allow_vault_reads=True)["permissions"]["allow"]
    vault_real = str(Path(os.path.realpath(provider.vault_root)))
    assert allow == [f"{tool}(/{vault_real}/**)" for tool in ("Read", "Glob", "Grep", "LS")]


# --- pure seams: argv -------------------------------------------------------


def test_argv_prepends_egress_wrapper_and_has_flags(tmp_path: Path) -> None:
    wrapper = ["bwrap", "--dev-bind", "/", "/", "--unshare-net"]
    provider = _provider(tmp_path, egress_wrapper=wrapper)
    settings_path = tmp_path / "settings.json"
    argv = provider.build_argv(settings_path)
    assert argv[: len(wrapper)] == wrapper  # PREPENDED verbatim
    assert "--settings" in argv and str(settings_path) in argv
    assert "--allowedTools" in argv
    assert "--permission-mode" in argv
    # stream-json (+ its required --verbose) is what exposes per-tool is_error.
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    tools = argv[argv.index("--allowedTools") + 1]
    assert tools == ""


def test_hosted_mode_requires_egress_wrapper(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="egress wrapper"):
        _provider(tmp_path, runtime_mode="hosted")


def test_hosted_mode_rejects_caller_supplied_noop_wrapper(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="exact built-in"):
        _provider(tmp_path, runtime_mode="hosted", egress_wrapper=["true"])


def test_hosted_wrapper_uses_isolated_python_import_mode(tmp_path: Path) -> None:
    planted = tmp_path / "mootloop"
    planted.mkdir()
    (planted / "__init__.py").write_text("raise SystemExit('shadowed')\n", encoding="utf-8")
    wrapper = hosted_wrapper()

    assert wrapper[:4] == [sys.executable, "-I", "-m", "mootloop.engine.egress_exec"]
    probe = subprocess.run(
        [sys.executable, "-I", "-c", "import mootloop; print(mootloop.__file__)"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert str(planted) not in probe.stdout


def test_hosted_mode_requires_fixed_authenticated_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOOTLOOP_EGRESS_PROXY_URL", "http://not-allowlisted:3128")
    monkeypatch.setenv("MOOTLOOP_EGRESS_PROXY_PASSWORD", "proxy-secret")
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(tmp_path / "engine-config"))
    provider = _provider(
        tmp_path,
        runtime_mode="hosted",
        egress_wrapper=hosted_wrapper(),
    )
    with pytest.raises(EgressError, match="egress-proxy"):
        provider.build_env()


def test_hosted_missing_proxy_auth_fails_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = tmp_path / "subprocess-called"
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ntouch {called}\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("MOOTLOOP_EGRESS_PROXY_URL", "http://egress-proxy:3128")
    monkeypatch.delenv("MOOTLOOP_EGRESS_PROXY_PASSWORD", raising=False)
    monkeypatch.setattr("mootloop.engine.isolation.secrets.load_secret", lambda key: None)
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(tmp_path / "engine-config"))
    provider = _provider(
        tmp_path,
        runtime_mode="hosted",
        egress_wrapper=hosted_wrapper(),
        claude_bin=str(fake),
    )

    with pytest.raises(EgressError, match="PROXY_PASSWORD"):
        provider.run_turn(_spec(), "safe prompt")
    assert not called.exists()


@pytest.mark.parametrize(
    ("prompt", "register"),
    [
        ("MOOTLOOP-CANARY-provider-boundary", False),
        ("registered-provider-secret-u02", True),
    ],
)
def test_outbound_prompt_tripwire_blocks_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prompt: str,
    register: bool,
) -> None:
    called = tmp_path / "subprocess-called"
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ntouch {called}\n", encoding="utf-8")
    fake.chmod(0o755)
    registry = tmp_path / "canaries.json"
    registry.write_text(
        '{"canaries":{"MOOTLOOP-CANARY-provider-boundary":"matter"},"denylist":[]}',
        encoding="utf-8",
    )
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(registry))
    if register:
        secrets.register_secret(prompt)
    provider = _provider(tmp_path, claude_bin=str(fake))

    with pytest.raises(OutboundPrivacyError, match="outbound payload contains"):
        provider.run_turn(_spec(), prompt)
    assert not called.exists()


def test_loader_supplied_secret_prompt_blocks_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "loader-only-exact-secret-u02"
    called = tmp_path / "subprocess-called"
    fake = tmp_path / "claude"
    fake.write_text(f"#!/bin/sh\ntouch {called}\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(tmp_path / "missing-registry"))
    provider = _provider(
        tmp_path,
        claude_bin=str(fake),
        oauth_token_loader=lambda: token,
    )

    with pytest.raises(OutboundPrivacyError, match="exact secret"):
        provider.run_turn(_spec(), token)
    assert not called.exists()


def test_hosted_env_routes_model_traffic_only_to_authenticated_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MOOTLOOP_EGRESS_PROXY_URL", "http://egress-proxy:3128")
    monkeypatch.setattr("mootloop.engine.isolation.secrets.load_secret", lambda key: "proxy pass")
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(tmp_path / "engine-config"))
    canary_registry = tmp_path / "global-canaries.json"
    canary_registry.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(canary_registry))
    provider = _provider(tmp_path, runtime_mode="hosted", egress_wrapper=hosted_wrapper())

    env = provider.build_env()

    assert env["HTTP_PROXY"] == env["HTTPS_PROXY"]
    assert env["HTTPS_PROXY"] == (
        "http://mootloop-model:proxy%20pass@egress-proxy:3128"
    )
    assert env["NO_PROXY"] == ""
    assert env["MOOTLOOP_CANARY_REGISTRY"] == str(canary_registry)
    assert validate_isolated_command(["claude", "-p"], env) == ["claude", "-p"]
    assert env["HOME"].startswith(env["CLAUDE_CONFIG_DIR"])
    assert env["TMPDIR"].startswith(env["CLAUDE_CONFIG_DIR"])


@pytest.mark.parametrize("registry_kind", ["missing", "directory", "symlink"])
def test_hosted_mode_requires_regular_canary_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry_kind: str
) -> None:
    monkeypatch.setenv("MOOTLOOP_EGRESS_PROXY_URL", "http://egress-proxy:3128")
    monkeypatch.setattr("mootloop.engine.isolation.secrets.load_secret", lambda key: "proxy pass")
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(tmp_path / "engine-config"))
    registry = tmp_path / "canaries.json"
    if registry_kind == "directory":
        registry.mkdir()
    elif registry_kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text("{}", encoding="utf-8")
        registry.symlink_to(target)
    monkeypatch.setenv("MOOTLOOP_CANARY_REGISTRY", str(registry))
    provider = _provider(tmp_path, runtime_mode="hosted", egress_wrapper=hosted_wrapper())

    with pytest.raises(EgressError, match="canary registry"):
        provider.build_env()


def test_hosted_wrapper_landlock_hides_matter_control_and_secrets(tmp_path: Path) -> None:
    vault = tmp_path / "worker" / "bound-matter"
    config = tmp_path / "config"
    queue = tmp_path / "worker" / ".queue"
    secrets_dir = tmp_path / "secrets"
    canary_registry = tmp_path / "global-canaries.json"
    for path in (vault, config, queue, secrets_dir):
        path.mkdir(parents=True, exist_ok=True)
    canary_registry.write_text("{}", encoding="utf-8")
    env = {
        "HTTP_PROXY": "http://mootloop-model:secret@egress-proxy:3128",
        "HTTPS_PROXY": "http://mootloop-model:secret@egress-proxy:3128",
        "MOOTLOOP_PROVIDER_VAULT": str(vault),
        "MOOTLOOP_PROVIDER_CONFIG_DIR": str(config),
        "MOOTLOOP_CONTROL_DIR": str(queue),
        "MOOTLOOP_SECRETS_DIR": str(secrets_dir),
        "MOOTLOOP_CANARY_REGISTRY": str(canary_registry),
    }

    (vault / "matter.txt").write_text("matter", encoding="utf-8")
    (queue / "control.txt").write_text("control", encoding="utf-8")
    (secrets_dir / "secret.txt").write_text("secret", encoding="utf-8")
    (config / "allowed.txt").write_text("allowed", encoding="utf-8")
    paths = [
        str(vault / "matter.txt"),
        str(queue / "control.txt"),
        str(secrets_dir / "secret.txt"),
        str(canary_registry),
    ]
    probe = (
        "import json,pathlib; paths="
        + repr(paths)
        + "; out=[]; "
        "exec('for p in paths:\\n try: pathlib.Path(p).read_text(); out.append(True)"
        "\\n except OSError: out.append(False)'); "
        f"print(json.dumps([out, pathlib.Path({str(config / 'allowed.txt')!r}).read_text()]))"
    )
    completed = subprocess.run(
        [*hosted_wrapper(), "/usr/bin/python3", "-c", probe],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )

    blocked, allowed = json.loads(completed.stdout)
    assert blocked == [False, False, False, False]
    assert allowed == "allowed"


def test_hosted_wrapper_exposes_only_required_proc_self_maps(tmp_path: Path) -> None:
    """Claude Code's native binary aborts when Landlock hides ``/proc/self/maps``.

    The exception must stay process-local: another live PID's maps file and an unrelated
    procfs identity/status file remain blocked.
    """
    vault = tmp_path / "worker" / "bound-matter"
    config = tmp_path / "config"
    queue = tmp_path / "worker" / ".queue"
    secrets_dir = tmp_path / "secrets"
    canary_registry = tmp_path / "global-canaries.json"
    for path in (vault, config, queue, secrets_dir):
        path.mkdir(parents=True, exist_ok=True)
    canary_registry.write_text("{}", encoding="utf-8")
    env = {
        "HTTP_PROXY": "http://mootloop-model:secret@egress-proxy:3128",
        "HTTPS_PROXY": "http://mootloop-model:secret@egress-proxy:3128",
        "MOOTLOOP_PROVIDER_VAULT": str(vault),
        "MOOTLOOP_PROVIDER_CONFIG_DIR": str(config),
        "MOOTLOOP_CONTROL_DIR": str(queue),
        "MOOTLOOP_SECRETS_DIR": str(secrets_dir),
        "MOOTLOOP_CANARY_REGISTRY": str(canary_registry),
    }
    probe = (
        "import json,os,pathlib; out=[]; "
        "paths=('/proc/self/maps',f'/proc/{os.getppid()}/maps','/proc/self/cgroup'); "
        "exec(\"for p in paths:\\n"
        " try: pathlib.Path(p).read_text(); out.append(True)"
        "\\n except OSError: out.append(False)\"); "
        "print(json.dumps(out))"
    )

    completed = subprocess.run(
        [*hosted_wrapper(), "/usr/bin/python3", "-c", probe],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(completed.stdout) == [True, False, False]


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("HTTP_PROXY", "http://egress-proxy:3128", "proxy"),
        ("HTTPS_PROXY", "http://mootloop:secret@other-proxy:3128", "proxy"),
        (
            "HTTPS_PROXY",
            "http://mootloop-legal:secret@egress-proxy:3128",
            "proxy",
        ),
        ("MOOTLOOP_PROVIDER_VAULT", "relative/vault", "paths"),
        ("MOOTLOOP_CANARY_REGISTRY", "", "paths"),
    ],
)
def test_hosted_wrapper_rejects_incomplete_or_untrusted_boundary(
    tmp_path: Path, key: str, value: str, message: str
) -> None:
    paths = {
        "MOOTLOOP_PROVIDER_VAULT": str(tmp_path / "vault"),
        "MOOTLOOP_PROVIDER_CONFIG_DIR": str(tmp_path / "config"),
        "MOOTLOOP_CONTROL_DIR": str(tmp_path / "queue"),
        "MOOTLOOP_SECRETS_DIR": str(tmp_path / "secrets"),
        "MOOTLOOP_CANARY_REGISTRY": str(tmp_path / "canaries.json"),
    }
    env = {
        **paths,
        "HTTP_PROXY": "http://mootloop-model:secret@egress-proxy:3128",
        "HTTPS_PROXY": "http://mootloop-model:secret@egress-proxy:3128",
    }
    env[key] = value

    with pytest.raises(SystemExit, match=message):
        validate_isolated_command(["claude", "-p"], env)


def test_hosted_wrapper_rejects_empty_command() -> None:
    with pytest.raises(SystemExit, match="requires a command"):
        validate_isolated_command([], {})


def test_proxy_service_prepares_auth_file_before_exec(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mootloop.engine import proxy_service

    password_file = tmp_path / "squid-passwords"
    source_password = tmp_path / "proxy-password"
    source_password.write_text("proxy-secret\n", encoding="utf-8")
    legal_password = tmp_path / "legal-proxy-password"
    legal_password.write_text("legal-proxy-secret\n", encoding="utf-8")
    executed: list[str] = []
    monkeypatch.setattr(proxy_service, "PASSWORD_FILE", str(password_file))
    monkeypatch.setenv(proxy_service.PROXY_PASSWORD_FILE_ENV, str(source_password))
    monkeypatch.setenv(proxy_service.LEGAL_PROXY_PASSWORD_FILE_ENV, str(legal_password))
    monkeypatch.setattr(
        proxy_service.os,
        "execvp",
        lambda executable, argv: executed.extend([executable, *argv]),
    )

    proxy_service.main()

    password_lines = password_file.read_text(encoding="utf-8").splitlines()
    model_identity, model_hash = password_lines[0].split(":", maxsplit=1)
    legal_identity, legal_hash = password_lines[1].split(":", maxsplit=1)
    assert model_identity == "mootloop-model"
    assert model_hash.startswith("$6$")
    model_salt = model_hash.split("$")[2]
    model_check = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", model_salt, "-stdin"],
        input="proxy-secret",
        check=True,
        capture_output=True,
        text=True,
    )
    assert model_check.stdout.strip() == model_hash
    assert legal_identity == "mootloop-legal"
    assert legal_hash.startswith("$6$")
    legal_salt = legal_hash.split("$")[2]
    legal_check = subprocess.run(
        ["openssl", "passwd", "-6", "-salt", legal_salt, "-stdin"],
        input="legal-proxy-secret",
        check=True,
        capture_output=True,
        text=True,
    )
    assert legal_check.stdout.strip() == legal_hash
    assert password_file.stat().st_mode & 0o777 == 0o600
    assert executed == ["squid", "squid", "-NYCd", "1"]


@pytest.mark.parametrize(
    "hash_error",
    [OSError("openssl missing"), subprocess.CalledProcessError(1, ["openssl"])],
)
def test_proxy_service_fails_closed_when_password_hashing_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
    hash_error: Exception,
) -> None:
    from mootloop.engine import proxy_service

    def fail_hash(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        raise hash_error

    monkeypatch.setattr(proxy_service.subprocess, "run", fail_hash)

    with pytest.raises(SystemExit, match="could not hash"):
        proxy_service._password_line(proxy_service.ProxyIdentity.MODEL, "proxy-secret")


@pytest.mark.parametrize("identity", list(ProxyIdentity))
def test_proxy_service_accepts_maximum_password_length(
    monkeypatch: pytest.MonkeyPatch,
    identity: ProxyIdentity,
) -> None:
    from mootloop.engine import proxy_service

    password = "x" * 256
    calls: list[str] = []

    def hash_password(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(str(kwargs["input"]))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="$6$salt$hash\n")

    monkeypatch.setattr(proxy_service.subprocess, "run", hash_password)

    assert proxy_service._password_line(identity, password) == f"{identity}:$6$salt$hash\n"
    assert calls == [password]


@pytest.mark.parametrize("identity", list(ProxyIdentity))
def test_proxy_service_rejects_overlong_password_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
    identity: ProxyIdentity,
) -> None:
    from mootloop.engine import proxy_service

    monkeypatch.setattr(
        proxy_service.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("OpenSSL must not receive an overlong password"),
    )

    with pytest.raises(SystemExit, match="at most 256 UTF-8 bytes"):
        proxy_service._password_line(identity, "x" * 257)


@pytest.mark.parametrize("hash_output", ["bad", "$5$salt$hash", "$6$$hash", "$6$salt$"])
def test_proxy_service_fails_closed_on_malformed_password_hash(
    monkeypatch: pytest.MonkeyPatch,
    hash_output: str,
) -> None:
    from mootloop.engine import proxy_service

    monkeypatch.setattr(
        proxy_service.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=f"{hash_output}\n"
        ),
    )

    with pytest.raises(SystemExit, match="could not hash"):
        proxy_service._password_line(proxy_service.ProxyIdentity.MODEL, "proxy-secret")


@pytest.mark.parametrize("password_kind", ["missing", "directory", "symlink", "empty"])
def test_proxy_service_fails_closed_on_invalid_password_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    password_kind: str,
) -> None:
    from mootloop.engine import proxy_service

    source = tmp_path / "proxy-password"
    legal_source = tmp_path / "legal-proxy-password"
    legal_source.write_text("legal-proxy-secret\n", encoding="utf-8")
    if password_kind == "directory":
        source.mkdir()
    elif password_kind == "symlink":
        target = tmp_path / "target"
        target.write_text("proxy-secret\n", encoding="utf-8")
        source.symlink_to(target)
    elif password_kind == "empty":
        source.write_text("", encoding="utf-8")
    monkeypatch.setenv(proxy_service.PROXY_PASSWORD_FILE_ENV, str(source))
    monkeypatch.setenv(proxy_service.LEGAL_PROXY_PASSWORD_FILE_ENV, str(legal_source))
    monkeypatch.setattr(
        proxy_service.os,
        "execvp",
        lambda executable, argv: pytest.fail("Squid must not start"),
    )

    with pytest.raises(SystemExit, match="regular|unreadable|empty"):
        proxy_service.main()


def test_proxy_service_rejects_shared_model_and_legal_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mootloop.engine import proxy_service

    model_source = tmp_path / "model-password"
    legal_source = tmp_path / "legal-password"
    model_source.write_text("shared-secret\n", encoding="utf-8")
    legal_source.write_text("shared-secret\n", encoding="utf-8")
    monkeypatch.setenv(proxy_service.PROXY_PASSWORD_FILE_ENV, str(model_source))
    monkeypatch.setenv(proxy_service.LEGAL_PROXY_PASSWORD_FILE_ENV, str(legal_source))
    monkeypatch.setattr(
        proxy_service.os,
        "execvp",
        lambda executable, argv: pytest.fail("Squid must not start"),
    )

    with pytest.raises(SystemExit, match="must differ"):
        proxy_service.main()


@pytest.mark.parametrize("mode", ["local", "dev"])
def test_local_and_dev_modes_are_explicit_and_do_not_inject_proxy(
    tmp_path: Path, mode: str
) -> None:
    env = _provider(tmp_path, runtime_mode=mode).build_env()
    assert "HTTPS_PROXY" not in env


def test_argv_appends_resume_when_session_present(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    key = provider._session_key(_spec())
    provider._persist_session_id(key, "sess-123")
    settings_path = provider._write_settings()
    argv = provider.build_argv(settings_path, session_id=provider._load_session_id(key))
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


_FENCED_BODY = """
import json
print(json.dumps({
    "result": "```json\\n{\\"response_text\\": \\"Admitted.\\"}\\n```",
    "session_id": "sess-fake-2",
    "usage": {"input_tokens": 10, "output_tokens": 5,
              "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
    "model": "claude-opus-4-8",
}))
"""


def test_run_turn_unwraps_fenced_json_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reply that is one ```json fenced block is unwrapped to its raw JSON payload
    (the live FE-7 failure mode: valid DraftOutput JSON wrapped in a markdown fence)."""
    _install_fake_claude(tmp_path / "bin", _FENCED_BODY, monkeypatch)
    result = _provider(tmp_path).run_turn(_spec(), "the prompt")
    assert result.text == '{"response_text": "Admitted."}'


def test_unfence_strips_single_wrapping_block() -> None:
    assert _unfence('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert _unfence('```\n{"a": 1}\n```') == '{"a": 1}'
    assert _unfence('  ```json\n{"a": 1}\n```  \n') == '{"a": 1}'


def test_unfence_leaves_plain_and_partial_text_alone() -> None:
    assert _unfence('{"a": 1}') == '{"a": 1}'
    assert _unfence("plain prose") == "plain prose"
    # Prose around a fence is NOT one wrapped block — schema validation must see it.
    assert _unfence('intro ```json\n{"a": 1}\n``` outro') == 'intro ```json\n{"a": 1}\n``` outro'
    # Multiple blocks stay untouched (greedy DOTALL must not merge them).
    two = '```json\n{"a": 1}\n```\nmiddle\n```json\n{"b": 2}\n```'
    assert _unfence(two) == two


# --- fail closed when the sandbox refuses a tool -----------------------------

# A denied turn as `claude` 2.1.222 actually reports it: the Read tool result carries
# `is_error: true` with the CLI's verbatim refusal, and the TERMINAL event still says
# `is_error: false` and exits 0. The old code returned the apology as the answer.
_DENIED_STREAM_BODY = r"""
import json, sys
for event in [
    {"type": "system", "subtype": "init", "session_id": "sess-denied"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "/vault/exhibit-a.txt"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True,
         "content": "<tool_use_error>File is in a directory that is denied by your "
                    "permission settings.</tool_use_error>"}]}},
    {"type": "result", "is_error": False, "session_id": "sess-denied",
     "result": "I need permission to read that file. Would you like to grant access?",
     "usage": {"input_tokens": 9, "output_tokens": 3}},
]:
    sys.stdout.write(json.dumps(event) + "\n")
"""

# The other observed shape: no error-flagged tool result reaches the stream, but the
# terminal event lists what was refused.
_DENIALS_FIELD_BODY = r"""
import json
print(json.dumps({
    "type": "result", "is_error": False, "session_id": "s",
    "result": "GLOB=fail GREP=fail",
    "permission_denials": [
        {"tool_name": "Glob", "tool_input": {"pattern": "*.txt"}},
        {"tool_name": "Grep", "tool_input": {"pattern": "x"}},
    ],
    "usage": {"input_tokens": 1, "output_tokens": 1},
}))
"""


def test_run_turn_fails_closed_when_a_tool_was_permission_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """REGRESSION. A denied turn exits 0 with a terminal `is_error: false`, so
    `returncode != 0` never fired and the model's apology for being unable to open the
    vault was parsed, scored and filed as the persona's work product."""
    _install_fake_claude(tmp_path / "bin", _DENIED_STREAM_BODY, monkeypatch)
    with pytest.raises(TurnError, match="denied filesystem access"):
        _provider(tmp_path).run_turn(_spec(), "the prompt")


def test_run_turn_fails_closed_on_reported_permission_denials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(tmp_path / "bin", _DENIALS_FIELD_BODY, monkeypatch)
    with pytest.raises(TurnError, match="Glob, Grep"):
        _provider(tmp_path).run_turn(_spec(), "the prompt")


# Persona prose that merely DISCUSSES denial — a discovery-dispute filing is full of it.
# The detector reads tool results the CLI flagged, never the reply, so this must pass.
_DISCOVERY_PROSE_BODY = r"""
import json, sys
for event in [
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "drafting"}]}},
    {"type": "result", "is_error": False, "session_id": "s",
     "result": "Plaintiff's motion to compel is denied by your permission settings "
               "argument, which misreads Rule 37; permission to read the file was "
               "never at issue. Request No. 4 is DENIED as overbroad.",
     "usage": {"input_tokens": 5, "output_tokens": 5}},
]:
    sys.stdout.write(json.dumps(event) + "\n")
"""


def test_denial_detector_ignores_persona_prose_about_denials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both marker strings appear verbatim in the reply text; neither appears in an
    error-flagged tool result. A legitimate turn must not be destroyed by its subject."""
    _install_fake_claude(tmp_path / "bin", _DISCOVERY_PROSE_BODY, monkeypatch)
    result = _provider(tmp_path).run_turn(_spec(), "the prompt")
    assert "DENIED as overbroad" in result.text


_TOP_LEVEL_ERROR_BODY = r"""
import json
print(json.dumps({"type": "result", "is_error": True, "session_id": "s",
                  "result": "Not logged in - Please run /login", "subtype": "error"}))
"""


def test_run_turn_fails_on_terminal_is_error_even_at_exit_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_claude(tmp_path / "bin", _TOP_LEVEL_ERROR_BODY, monkeypatch)
    with pytest.raises(TurnError, match="reported an error"):
        _provider(tmp_path).run_turn(_spec(), "the prompt")


# --- the prompt is privileged: keep it off argv ------------------------------

# Echoes back what the process could see: its own argv, and whatever arrived on stdin.
_ECHO_BODY = r"""
import json, sys
print(json.dumps({
    "type": "result", "is_error": False, "session_id": "s",
    "result": json.dumps({"argv": sys.argv[1:], "stdin": sys.stdin.read()}),
    "usage": {"input_tokens": 1, "output_tokens": 1},
}))
"""

_PRIVILEGED = "PRIVILEGED-WORK-PRODUCT-b3f1e0"


def test_prompt_travels_on_stdin_not_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """REGRESSION. The persona prompt is privileged work product and was passed as an
    argv element, where `/proc/<pid>/cmdline` exposes it to every local process for the
    life of the turn — and where `subprocess.TimeoutExpired.__str__` embedded it into a
    chained traceback."""
    _install_fake_claude(tmp_path / "bin", _ECHO_BODY, monkeypatch)
    provider = _provider(tmp_path)
    seen = json.loads(provider.run_turn(_spec(), _PRIVILEGED).text)
    assert _PRIVILEGED not in " ".join(seen["argv"])
    assert seen["stdin"] == _PRIVILEGED
    assert _PRIVILEGED not in " ".join(provider.build_argv(tmp_path / "s.json"))


def test_timeout_does_not_chain_the_prompt_bearing_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`TimeoutExpired` renders the child's argv and captured output; nothing from the
    child may ride into an exception that gets logged."""
    _install_fake_claude(tmp_path / "bin", "import time\ntime.sleep(5)\n", monkeypatch)
    provider = _provider(tmp_path, timeout_s=0.5)
    with pytest.raises(TurnError, match="timed out") as caught:
        provider.run_turn(_spec(), _PRIVILEGED)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert _PRIVILEGED not in str(caught.value)


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
    assert "Read(//**)" in deny  # every direct filesystem read is denied
    assert any(".mootloop/secrets.env" in rule for rule in deny)  # secrets denied
    settings_path = provider._write_settings()
    assert str(settings_path) in provider.build_argv(settings_path)

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


# --- the CLI's credential store must never live in the vault ------------------


def test_config_dir_defaults_outside_the_vault(tmp_path: Path) -> None:
    """`CLAUDE_CONFIG_DIR` is where Claude Code persists `.credentials.json` — the
    subscription token. It defaulted to `<vault>/runs/<run>/claude-config`, i.e. inside
    the one tree `build_settings` grants `Read(<vault>/**)` on, and inside every
    `mootloop backup` archive (which excludes only `<matter>/staging`)."""
    provider = _provider(tmp_path)
    vault_real = Path(os.path.realpath(provider.vault_root))
    config_real = Path(os.path.realpath(provider._config_dir()))
    assert config_real != vault_real
    assert vault_real not in config_real.parents, (
        f"the credential store {config_real} is inside the vault {vault_real}"
    )
    assert provider.build_env()["CLAUDE_CONFIG_DIR"] == str(provider._config_dir())


def test_config_dir_honours_the_env_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hosted deployments mount `~/.mootloop` read-only (see MOOTLOOP_CANARY_REGISTRY)."""
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(tmp_path / "engine-cfg"))
    provider = _provider(tmp_path)
    assert str(provider._config_dir()).startswith(str(tmp_path / "engine-cfg"))


def test_hosted_config_dir_uses_bound_identity_not_mount_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine_root = tmp_path / "engine-cfg"
    monkeypatch.setenv(ENGINE_CONFIG_ENV, str(engine_root))
    provider = _provider(
        tmp_path,
        runtime_mode="hosted",
        egress_wrapper=hosted_wrapper(),
        matter_id="2026-08-21-acme.foo_test",
    )

    assert provider._config_dir() == engine_root / "2026-08-21-acme.foo_test" / "r1"


def test_hosted_provider_requires_bound_matter_identity(tmp_path: Path) -> None:
    vault = tmp_path / "matter"
    run_dir = vault / "runs" / "r1"
    run_dir.mkdir(parents=True)

    with pytest.raises(ValueError, match="bound matter id"):
        HeadlessClaudeProvider(
            vault_root=vault,
            run_dir=run_dir,
            runtime_mode="hosted",
            egress_wrapper=hosted_wrapper(),
        )


def test_hosted_provider_rejects_invalid_bound_matter_identity(tmp_path: Path) -> None:
    with pytest.raises(VaultBoundaryError, match="path components"):
        _provider(
            tmp_path,
            runtime_mode="hosted",
            egress_wrapper=hosted_wrapper(),
            matter_id="../other",
        )


def test_config_dir_is_per_run_so_sessions_resume(tmp_path: Path) -> None:
    """`--resume` needs a stable config dir per run, and two runs must not collide."""
    a = _provider(tmp_path, run_dir=tmp_path / "vault" / "runs" / "run-a")
    b = _provider(tmp_path, run_dir=tmp_path / "vault" / "runs" / "run-b")
    assert a._config_dir() != b._config_dir()
    assert a._config_dir() == _provider(tmp_path, run_dir=a.run_dir)._config_dir()


def test_settings_deny_the_credential_store(tmp_path: Path) -> None:
    provider = _provider(tmp_path)
    deny = provider.build_settings()["permissions"]["deny"]
    assert any(str(provider._config_dir()) in rule for rule in deny)


def test_argv_pins_the_tier_model(tmp_path: Path) -> None:
    """Without `--model` the CLI ran its own default, so the budget-tier map was
    decorative: a `low`-tier run reserved Haiku dollars and could burn Opus ones."""
    provider = _provider(tmp_path)
    argv = provider.build_argv(tmp_path / "settings.json", model="claude-haiku-4-5")
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-haiku-4-5"
    # Absent a planned model, no flag is emitted (the CLI keeps its default).
    assert "--model" not in provider.build_argv(tmp_path / "s.json")
