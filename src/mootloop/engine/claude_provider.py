"""Headless Claude provider (plan FE-1): drive a persona turn through the Claude Code
CLI (``claude -p``) as a subprocess, sandboxed to the matter vault.

This is the hosted-tier `LLMProvider`: instead of an HTTP model call, it shells out to
a headless ``claude`` binary running with the operator's Max-plan subscription token.
Every escape hatch is closed by construction:

- The subprocess sees a MINIMAL, explicitly-built environment (never ``os.environ``),
  carrying only the subscription OAuth token, a per-run config dir, and the
  auto-updater/telemetry kill switches.
- ``--allowedTools`` is a READ-ONLY allowlist (no Bash / Write / Edit / web tools).
- A per-run ``--settings`` file denies reads/writes outside the vault realpath and
  denies the secrets file outright.
- An optional ``egress_wrapper`` (e.g. a ``bwrap`` network jail) is PREPENDED to argv;
  the jail itself is deployment config, but the seam and the prepend live here.

The build seams (`build_settings` / `build_allowed_tools` / `build_env` / `build_argv`)
are pure so the sandbox can be asserted WITHOUT executing a real ``claude``. Failures
are classified from the subprocess output into `SeatLimitError` / `AuthError` /
`TurnError`; any surfaced stderr is `redact`-ed so a token can never leak.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mootloop import secrets
from mootloop.errors import AuthError, SeatLimitError, TurnError
from mootloop.llm import RawTurnResult, TokenUsage
from mootloop.models.run import TurnSpec
from mootloop.secrets import SECRETS_FILE, register_secret

# Read-only file tools ONLY. No Bash, Write, Edit, WebFetch, WebSearch, or any
# network/exec tool — a persona reads the vault and returns JSON, nothing more.
READ_ONLY_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep", "LS")

# Substring signatures (matched case-insensitively) that classify a failed turn.
_SEAT_SIGNATURES: tuple[str, ...] = ("rate limit", "rate_limit", "usage limit", "seat")
_AUTH_SIGNATURES: tuple[str, ...] = (
    "authentication_failed",
    "authentication failed",
    "unauthorized",
    "invalid api key",
    "oauth",
)

TokenLoader = Callable[[], str | None]
Clock = Callable[[], datetime]


def _load_oauth() -> str | None:
    return secrets.load_secret("CLAUDE_CODE_OAUTH_TOKEN")


def _load_api_key() -> str | None:
    return secrets.load_secret("ANTHROPIC_API_KEY")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _slug(value: str) -> str:
    """A filesystem-safe slug (``ROG-3(a)`` -> ``rog-3-a``) for a session-key filename."""
    out = "".join(ch if ch.isalnum() else "-" for ch in value.lower())
    return out.strip("-") or "none"


@dataclass
class HeadlessClaudeProvider:
    """`LLMProvider` backed by a sandboxed ``claude -p`` subprocess (plan FE-1)."""

    vault_root: Path | str
    run_dir: Path
    billing_mode: str = "subscription"
    claude_bin: str = "claude"
    egress_wrapper: list[str] = field(default_factory=list)
    config_dir: Path | None = None
    oauth_token_loader: TokenLoader | None = None
    api_key_loader: TokenLoader | None = None
    max_turns: int = 6
    timeout_s: float = 600.0
    now: Clock = _utcnow

    def __post_init__(self) -> None:
        self.run_dir = Path(self.run_dir)

    # -- resolved paths --

    def _vault_real(self) -> Path:
        return Path(os.path.realpath(self.vault_root))

    def _config_dir(self) -> Path:
        if self.config_dir is not None:
            return Path(self.config_dir)
        return self.run_dir / "claude-config"

    def _secrets_real(self) -> Path:
        return Path(os.path.realpath(SECRETS_FILE))

    # -- pure seams (unit-3 tests inspect these without executing claude) --

    def build_settings(self) -> dict[str, Any]:
        """The per-run ``--settings`` dict: deny everything outside the vault and deny
        the secrets file; allow only read-only tools scoped to the vault realpath."""
        vault = str(self._vault_real())
        secrets_path = str(self._secrets_real())
        secrets_dir = str(self._secrets_real().parent)
        return {
            "permissions": {
                # Deny reads/writes anywhere on disk, then re-allow only the vault.
                "deny": [
                    "Read(/**)",
                    "Write(/**)",
                    "Edit(/**)",
                    f"Read({secrets_path})",
                    f"Read({secrets_dir}/**)",
                    "Bash",
                    "WebFetch",
                    "WebSearch",
                ],
                "allow": [f"{tool}({vault}/**)" for tool in READ_ONLY_TOOLS],
            }
        }

    def build_allowed_tools(self) -> list[str]:
        """The read-only ``--allowedTools`` list (no Bash / Write / Edit / web tools)."""
        return list(READ_ONLY_TOOLS)

    def build_env(self) -> dict[str, str]:
        """The subprocess environment, built EXPLICITLY from a minimal base.

        Never copies ``os.environ`` wholesale. Subscription mode carries the OAuth
        token (registered for verbatim redaction) and no API key; ``api`` mode carries
        the API key and no OAuth token. Fails closed if the required credential is
        missing (a run can never fall through to an unauthenticated call)."""
        env: dict[str, str] = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", str(self.run_dir)),
            "CLAUDE_CONFIG_DIR": str(self._config_dir()),
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_TELEMETRY": "1",
        }
        if self.billing_mode == "api":
            api_key = (self.api_key_loader or _load_api_key)()
            if not api_key:
                raise AuthError("ANTHROPIC_API_KEY is not configured for api billing mode")
            register_secret(api_key)
            env["ANTHROPIC_API_KEY"] = api_key
        else:
            token = (self.oauth_token_loader or _load_oauth)()
            if not token:
                raise AuthError("CLAUDE_CODE_OAUTH_TOKEN is not configured")
            register_secret(token)
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        return env

    def build_argv(
        self, prompt: str, settings_path: Path, *, session_id: str | None = None
    ) -> list[str]:
        """The full argv: ``egress_wrapper`` PREPENDED, then the non-interactive
        ``claude -p`` invocation, with ``--resume`` appended when a session persists."""
        argv = [
            *self.egress_wrapper,
            self.claude_bin,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--max-turns",
            str(self.max_turns),
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            ",".join(self.build_allowed_tools()),
            "--settings",
            str(settings_path),
        ]
        if session_id:
            argv += ["--resume", session_id]
        return argv

    # -- session persistence (keyed by persona + request) --

    def _session_key(self, spec: TurnSpec) -> str:
        request = str(spec.request_id) if spec.request_id else "none"
        return f"{_slug(spec.persona.value)}-{_slug(request)}"

    def _session_file(self, key: str) -> Path:
        # Contained under run_dir/sessions via the same realpath choke-point discipline.
        from mootloop.vault import safe_vault_path

        return safe_vault_path(self.run_dir, "sessions", f"{key}.json")

    def _load_session_id(self, key: str) -> str | None:
        path = self._session_file(key)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        session_id = data.get("session_id") if isinstance(data, dict) else None
        return session_id if isinstance(session_id, str) else None

    def _persist_session_id(self, key: str, session_id: str) -> None:
        from mootloop.vault import atomic_write_text

        path = self._session_file(key)
        atomic_write_text(path, json.dumps({"session_id": session_id}) + "\n")

    def _write_settings(self) -> Path:
        from mootloop.vault import atomic_write_text, safe_vault_path

        path = safe_vault_path(self.run_dir, "settings.json")
        atomic_write_text(path, json.dumps(self.build_settings(), indent=2) + "\n")
        return path

    # -- provider protocol --

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        import subprocess  # local import: the module imports without a real claude bin

        key = self._session_key(spec)
        session_id = self._load_session_id(key)
        settings_path = self._write_settings()
        argv = self.build_argv(prompt, settings_path, session_id=session_id)
        env = self.build_env()
        try:
            completed = subprocess.run(  # noqa: S603 — argv is fully constructed here
                argv,
                cwd=str(self._vault_real()),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise TurnError(f"headless turn timed out after {self.timeout_s}s") from exc
        except OSError as exc:
            raise TurnError(f"headless turn could not start: {exc}") from exc

        if completed.returncode != 0:
            self._raise_classified(completed.stdout, completed.stderr, completed.returncode)
        return self._parse_result(completed.stdout, key)

    # -- result parsing + failure classification --

    def _parse_result(self, stdout: str, key: str) -> RawTurnResult:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise TurnError("headless turn returned unparseable JSON") from exc
        if not isinstance(payload, dict):
            raise TurnError("headless turn JSON was not an object")
        text = payload.get("result")
        if not isinstance(text, str):
            raise TurnError("headless turn JSON had no 'result' text")
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            self._persist_session_id(key, session_id)
        usage = self._usage_from(payload)
        return RawTurnResult(text=text, usage=usage)

    @staticmethod
    def _usage_from(payload: dict[str, Any]) -> TokenUsage | None:
        raw = payload.get("usage")
        if not isinstance(raw, dict):
            return None
        model = payload.get("model")

        def _int(*keys: str) -> int:
            for k in keys:
                v = raw.get(k)
                if isinstance(v, int):
                    return v
            return 0

        return TokenUsage(
            input_tokens=_int("input_tokens"),
            cache_read=_int("cache_read_input_tokens", "cache_read"),
            cache_write=_int("cache_creation_input_tokens", "cache_write"),
            output_tokens=_int("output_tokens"),
            model=model if isinstance(model, str) else "claude",
        )

    @staticmethod
    def _raise_classified(stdout: str, stderr: str, returncode: int) -> None:
        haystack = f"{stdout}\n{stderr}".lower()
        if any(sig in haystack for sig in _SEAT_SIGNATURES):
            raise SeatLimitError("headless Claude hit a seat/rate limit")
        if any(sig in haystack for sig in _AUTH_SIGNATURES):
            raise AuthError("headless Claude authentication failed")
        raise TurnError(
            f"headless turn failed (exit {returncode}): {secrets.redact(stderr).strip()[:500]}"
        )
