"""Headless Claude provider (plan FE-1): drive a persona turn through the Claude Code
CLI (``claude -p``) as a subprocess, sandboxed to the matter vault.

This is the hosted-tier `LLMProvider`: instead of an HTTP model call, it shells out to
a headless ``claude`` binary running with the operator's Max-plan subscription token.
Every escape hatch is closed by construction:

- The subprocess sees a MINIMAL, explicitly-built environment (never ``os.environ``),
  carrying only the subscription OAuth token, a per-run config dir, and the
  auto-updater/telemetry kill switches.
- ``--allowedTools`` is empty for persona turns; approved inputs arrive through the
  immutable prompt context instead of direct vault reads.
- A per-run ``--settings`` file denies reads outside the vault realpath — by enumerating
  the vault's siblings at every ancestor level, NOT by a blanket ``Read(/**)``, which
  ``deny``-beats-``allow`` turns into a total filesystem blackout (see
  `_outside_vault_read_deny`) — and denies the secrets file outright. Every rule path is
  anchored at the filesystem root with a DOUBLE leading slash (see `_abs`).
- The persona prompt goes in on STDIN, never argv (`/proc/<pid>/cmdline` is world-readable).
- A turn whose tools were refused is FAILED, not returned: the CLI exits 0 with a
  terminal ``is_error: false`` even then, so the per-tool ``is_error`` in the
  ``stream-json`` output is the only honest signal (see `_permission_denial`).
- An optional ``egress_wrapper`` (the hosted Landlock preflight) is PREPENDED to argv;
  the jail itself is deployment config, but the seam and the prepend live here.

The build seams (`build_settings` / `build_allowed_tools` / `build_env` / `build_argv`)
are pure so the sandbox can be asserted WITHOUT executing a real ``claude``. Failures
are classified from the subprocess output into `SeatLimitError` / `AuthError` /
`TurnError`; any surfaced stderr is `redact`-ed so a token can never leak.
"""

from __future__ import annotations

import json
import os
import re
import stat
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mootloop import secrets
from mootloop.engine.isolation import (
    CONTROL_DIR_ENV,
    PROVIDER_CONFIG_DIR_ENV,
    PROVIDER_VAULT_ENV,
    SECRETS_DIR_ENV,
    HostedProxy,
    hosted_wrapper,
)
from mootloop.errors import AuthError, EgressError, SeatLimitError, TurnError
from mootloop.llm import RawTurnResult, TokenUsage
from mootloop.models.run import TurnSpec
from mootloop.privacy import CANARY_REGISTRY_ENV, scrub_outbound
from mootloop.runtime import RuntimeMode, validate_runtime_mode
from mootloop.secrets import SECRETS_FILE, register_secret
from mootloop.vault import _is_within, _real, atomic_write_text, safe_vault_path

# The approved context assembler supplies persona inputs in the prompt. These tools
# remain only as an explicit diagnostic seam; normal persona turns receive none.
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

# Where Claude Code keeps its own state (including `.credentials.json`, which holds the
# subscription token). This MUST NOT be inside the matter vault: the vault is the one
# tree the persona sandbox opens for reading, and it is what `mootloop backup` archives.
# Overridable for hosted deployments whose `~/.mootloop` is a read-only mount, mirroring
# the `MOOTLOOP_CANARY_REGISTRY` convention.
ENGINE_CONFIG_ENV = "MOOTLOOP_ENGINE_CONFIG_DIR"
DEFAULT_ENGINE_CONFIG_DIR = Path.home() / ".mootloop" / "engine-config"


def engine_config_root() -> Path:
    override = os.environ.get(ENGINE_CONFIG_ENV)
    return Path(override) if override else DEFAULT_ENGINE_CONFIG_DIR


# A reply that is exactly one fenced code block (```json ... ```). The chat surface
# wraps structured output in a fence; unwrapping it is transport normalization, not
# output repair — schema validation downstream still rejects anything semantically off.
_FENCED_BLOCK_RE = re.compile(r"^```[A-Za-z0-9_-]*[ \t]*\r?\n(.*?)\r?\n?```\s*$", re.DOTALL)


def _unfence(text: str) -> str:
    """Strip a single wrapping markdown code fence, if the whole reply is one block.

    The inner-fence guard keeps multi-block replies untouched (backtracking under the
    ``$`` anchor would otherwise merge them into one span)."""
    match = _FENCED_BLOCK_RE.match(text.strip())
    if match is not None and "```" not in match.group(1):
        return match.group(1)
    return text


def _load_oauth() -> str | None:
    return secrets.load_secret("CLAUDE_CODE_OAUTH_TOKEN")


def _load_api_key() -> str | None:
    return secrets.load_secret("ANTHROPIC_API_KEY")


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Markers of a sandbox refusal, matched ONLY against tool results the CLI itself flagged
# `is_error: true` — never against persona prose. That distinction is the whole design:
# a filing about a discovery dispute may well contain "denied", and a persona paraphrasing
# its own refusal ("I don't have permission to…") is a symptom, not the signal. These two
# strings are the CLI's own verbatim wording for a blocked path.
_DENIAL_MARKERS: tuple[str, ...] = (
    "denied by your permission settings",
    "permission to read",
)


def _json_events(stdout: str) -> list[dict[str, Any]]:
    """Parse ``stream-json`` output into its event objects.

    Tolerates a single whole-document JSON object too, so a caller (or a test double)
    emitting ``--output-format json`` still parses."""
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    if events:
        return events
    try:
        whole = json.loads(stdout)
    except json.JSONDecodeError:
        return []
    return [whole] if isinstance(whole, dict) else []


def _final_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    """The terminal ``result`` event — the one carrying the reply text and usage."""
    for event in reversed(events):
        if event.get("type") == "result":
            return event
    return events[-1]


def _content_text(content: Any) -> str:
    """Flatten a tool-result ``content`` (string, or a list of content blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                parts.append(text if isinstance(text, str) else json.dumps(block))
        return "\n".join(parts)
    return json.dumps(content)


def _iter_error_tool_results(node: Any) -> Iterator[str]:
    """Yield the text of every ``is_error: true`` tool result anywhere in an event.

    Walks structurally rather than assuming a message shape, so a change to how the CLI
    nests tool results downgrades this to "found nothing new", never to a parse crash."""
    if isinstance(node, dict):
        if node.get("is_error") is True and "content" in node:
            yield _content_text(node["content"])
        for value in node.values():
            yield from _iter_error_tool_results(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_error_tool_results(item)


def _permission_denial(events: list[dict[str, Any]], payload: dict[str, Any]) -> str | None:
    """A short, redacted description of the sandbox refusal in this turn, or None."""
    for event in events:
        for text in _iter_error_tool_results(event):
            lowered = text.lower()
            if any(marker in lowered for marker in _DENIAL_MARKERS):
                return secrets.redact(" ".join(text.split()))[:200]
    denials = payload.get("permission_denials")
    if isinstance(denials, list) and denials:
        tools = sorted(
            {d.get("tool_name", "?") for d in denials if isinstance(d, dict)},
        )
        return f"the CLI reported permission_denials for {', '.join(str(t) for t in tools)}"
    return None


def _abs(path: Path | str) -> str:
    """Render ``path`` as a permission-rule pattern anchored at the FILESYSTEM root.

    Claude Code reads a rule path with a single leading slash as relative to the
    directory holding the settings file; only a DOUBLE leading slash means "absolute".
    Every absolute deny in the original settings — the secrets file, the secrets
    directory, the credential store — carried one slash, so none of them denied anything.
    They read as `<run_dir>/home/…`, a path that does not exist. Verified against
    ``claude`` 2.1.222: with ``Read(/<tmp>/outside/**)`` the file is returned; with
    ``Read(//<tmp>/outside/**)`` the same read comes back
    "File is in a directory that is denied by your permission settings."
    """
    return f"/{path}"


def _looks_like_dir(path: Path) -> bool:
    """Fail-closed ``is_dir``: an entry we cannot stat is treated as a directory, so the
    recursive ``/**`` deny is emitted rather than silently skipped."""
    try:
        return path.is_dir()
    except OSError:
        return True


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
    runtime_mode: RuntimeMode = RuntimeMode.LOCAL

    def __post_init__(self) -> None:
        self.run_dir = Path(self.run_dir)
        self.runtime_mode = validate_runtime_mode(self.runtime_mode)
        if self.runtime_mode is RuntimeMode.HOSTED and self.egress_wrapper != hosted_wrapper():
            raise ValueError("hosted mode requires the exact built-in egress wrapper")
        if self.runtime_mode is RuntimeMode.HOSTED:
            config_real = _real(self._config_dir())
            secrets_real = _real(SECRETS_FILE.parent)
            if _is_within(config_real, secrets_real):
                raise ValueError("hosted engine config must be outside the secrets mount")

    # -- resolved paths --

    def _vault_real(self) -> Path:
        return Path(os.path.realpath(self.vault_root))

    def _config_dir(self) -> Path:
        """Claude Code's state dir for this run — deliberately OUTSIDE the vault.

        It defaulted to ``<vault>/runs/<run_id>/claude-config``, which put the
        subscription token's `.credentials.json` inside the vault: inside the tree
        `build_settings` grants ``Read(<vault>/**)`` on, and inside every
        ``mootloop backup`` archive (`_exclude_staging` drops only ``<matter>/staging``).
        The deny list carefully closes the door on ``~/.mootloop/secrets.env`` while the
        credential sat in the room behind it. AGENTS.md: secrets live in
        ``~/.mootloop/secrets.env`` or the keychain — "never in matter.yaml, config, or
        the vault".
        """
        if self.config_dir is not None:
            return Path(self.config_dir)
        run_dir = self.run_dir
        matter = run_dir.parent.parent.name or "matter"
        return engine_config_root() / _slug(matter) / _slug(run_dir.name)

    def _secrets_real(self) -> Path:
        return Path(os.path.realpath(SECRETS_FILE))

    # -- pure seams (unit-3 tests inspect these without executing claude) --

    def _outside_vault_read_deny(self) -> list[str]:
        """Deny rules covering every path outside the vault, built by ENUMERATING the
        vault's siblings at each ancestor level.

        This exists because ``Read(/**)`` cannot be used. Claude Code evaluates ``deny``
        BEFORE ``allow`` and a denied path is never re-opened, so the old
        "deny ``Read(/**)``, then re-allow ``Read(<vault>/**)``" pairing denied the vault
        as well: the allow rule was dead on arrival. Worse, ``Read(/**)`` gates *path*
        read access rather than the tool named ``Read``, so ``Glob`` and ``Grep`` over the
        vault were denied too — a persona turn had no filesystem access of any kind and
        drafted from its prompt alone.

        The complement construction gets the documented posture with the semantics the
        permission engine actually has: walk from the vault up to ``/`` and deny every
        entry that is NOT on the vault's own path. The vault and its ancestors are the
        only paths left unmatched, so ``allow`` still has something to grant.

        Limits, stated plainly: the enumeration is a snapshot taken when the per-run
        settings file is written, so a directory created afterwards is not named here.
        That residual is covered by the second layer — the subprocess runs with
        ``cwd=<vault>`` under ``--permission-mode dontAsk`` and a per-run
        ``CLAUDE_CONFIG_DIR`` holding no user allow rules, so a path that is neither the
        workspace nor allow-listed is refused rather than prompted for.
        """
        vault = self._vault_real()
        rules: list[str] = []
        seen: set[str] = set()
        child = vault
        for parent in vault.parents:
            try:
                entries = sorted(parent.iterdir())
            except OSError:  # unreadable ancestor: nothing to enumerate, keep climbing
                entries = []
            for entry in entries:
                if entry == child:
                    continue
                path = _abs(entry)
                if path in seen:
                    continue
                seen.add(path)
                rules.append(f"Read({path})")
                if _looks_like_dir(entry):
                    rules.append(f"Read({path}/**)")
            child = parent
        return rules

    def build_settings(self, *, allow_vault_reads: bool = False) -> dict[str, Any]:
        """Build fail-closed settings; normal persona turns have no filesystem reads."""
        vault = _abs(self._vault_real())
        secrets_path = _abs(self._secrets_real())
        secrets_dir = _abs(self._secrets_real().parent)
        config_dir = _abs(self._config_dir())
        allowed_tools = self.build_allowed_tools(allow_vault_reads=allow_vault_reads)
        return {
            "permissions": {
                "deny": [
                    # Writes go nowhere. A blanket write deny is safe in a way a blanket
                    # read deny is not: no `allow` rule needs to survive it.
                    "Write(//**)",
                    "Edit(//**)",
                    "Bash",
                    "WebFetch",
                    "WebSearch",
                    *([] if allow_vault_reads else ["Read(//**)"]),
                    # The secrets file and its directory, named explicitly rather than
                    # left to the enumeration below.
                    f"Read({secrets_path})",
                    f"Read({secrets_dir}/**)",
                    # The CLI's own state dir holds `.credentials.json` — the
                    # subscription token. Deny this run's dir, the CLI's default dir, and
                    # any credential store anywhere.
                    f"Read({config_dir})",
                    f"Read({config_dir}/**)",
                    f"Read({_abs(engine_config_root())}/**)",
                    f"Read({_abs(Path.home() / '.claude')}/**)",
                    "Read(//**/.credentials.json)",
                    # Everything else outside the vault.
                    *(self._outside_vault_read_deny() if allow_vault_reads else []),
                ],
                "allow": (
                    [f"{tool}({vault}/**)" for tool in allowed_tools] if allow_vault_reads else []
                ),
            }
        }

    def build_allowed_tools(self, *, allow_vault_reads: bool = False) -> list[str]:
        """Return no tools for persona turns; opt-in reads exist only for diagnostics."""
        return list(READ_ONLY_TOOLS) if allow_vault_reads else []

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
        if self.runtime_mode is RuntimeMode.HOSTED:
            config_dir = self._config_dir()
            runtime_home = config_dir / "home"
            runtime_tmp = config_dir / "tmp"
            runtime_home.mkdir(mode=0o700, parents=True, exist_ok=True)
            runtime_tmp.mkdir(mode=0o700, parents=True, exist_ok=True)
            env["HOME"] = str(runtime_home)
            env["TMPDIR"] = str(runtime_tmp)
            proxy = HostedProxy.from_env()
            env["HTTP_PROXY"] = proxy.url
            env["HTTPS_PROXY"] = proxy.url
            env["NO_PROXY"] = ""
            env[PROVIDER_VAULT_ENV] = str(self._vault_real())
            env[PROVIDER_CONFIG_DIR_ENV] = str(self._config_dir())
            env[CONTROL_DIR_ENV] = str(self._vault_real().parent / ".queue")
            env[SECRETS_DIR_ENV] = str(self._secrets_real().parent)
            canary_registry = os.environ.get(CANARY_REGISTRY_ENV)
            if not canary_registry or not os.path.isabs(canary_registry):
                raise EgressError("hosted mode requires an absolute canary registry path")
            try:
                registry_mode = os.lstat(canary_registry).st_mode
            except OSError as exc:
                raise EgressError("hosted mode requires a regular canary registry file") from exc
            if not stat.S_ISREG(registry_mode):
                raise EgressError("hosted mode requires a regular canary registry file")
            env[CANARY_REGISTRY_ENV] = canary_registry
        return env

    def build_argv(
        self,
        settings_path: Path,
        *,
        session_id: str | None = None,
        model: str | None = None,
    ) -> list[str]:
        """The full argv: ``egress_wrapper`` PREPENDED, then the non-interactive
        ``claude -p`` invocation, with ``--resume`` appended when a session persists.

        The persona prompt is deliberately NOT here. It is privileged work product, and
        argv is world-readable through ``/proc/<pid>/cmdline`` for as long as the turn
        runs; it also lands verbatim in ``subprocess.TimeoutExpired``'s string form. The
        prompt is fed on stdin instead (`run_turn`), which no other local process can
        read.

        ``--output-format stream-json`` (with its required ``--verbose``) makes any
        attempted denied tool call detectable: the terminal ``result`` event can report
        ``is_error: false`` even after a refusal, so only the per-tool stream result is
        reliable.

        ``--model`` pins the tier's chosen model. Without it the CLI ran whatever it
        defaults to, which made the whole budget-tier map decorative: a ``low``-tier run
        reserved Haiku dollars against the cap and could burn Opus ones."""
        argv = [
            *self.egress_wrapper,
            self.claude_bin,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-turns",
            str(self.max_turns),
            "--permission-mode",
            "dontAsk",
            "--allowedTools",
            ",".join(self.build_allowed_tools()),
            "--settings",
            str(settings_path),
        ]
        if model:
            argv += ["--model", model]
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
        path = (
            self._config_dir() / "settings.json"
            if self.runtime_mode is RuntimeMode.HOSTED
            else safe_vault_path(self.run_dir, "settings.json")
        )
        atomic_write_text(path, json.dumps(self.build_settings(), indent=2) + "\n")
        return path

    # -- provider protocol --

    def run_turn(self, spec: TurnSpec, prompt: str) -> RawTurnResult:
        import subprocess  # local import: the module imports without a real claude bin

        # Register the exact credential literals first, then tripwire the payload.
        # This ordering blocks a loader-supplied token even when it is absent from the
        # secrets file and does so before settings/argv creation or subprocess launch.
        self._config_dir().mkdir(mode=0o700, parents=True, exist_ok=True)
        env = self.build_env()
        public_prompt = scrub_outbound(prompt)
        key = self._session_key(spec)
        session_id = self._load_session_id(key)
        settings_path = self._write_settings()
        argv = self.build_argv(settings_path, session_id=session_id, model=spec.model)
        completed: subprocess.CompletedProcess[str] | None
        try:
            completed = subprocess.run(  # noqa: S603 — argv is fully constructed here
                argv,
                input=public_prompt,  # NOT argv: /proc/<pid>/cmdline is world-readable
                cwd=str(
                    self._config_dir()
                    if self.runtime_mode is RuntimeMode.HOSTED
                    else self._vault_real()
                ),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired:
            completed = None
        except OSError as exc:
            raise TurnError(f"headless turn could not start: {exc}") from exc
        if completed is None:
            # Raised OUTSIDE the handler so `TimeoutExpired` is neither the cause nor the
            # context: it carries the child's argv and its captured partial stdout, which
            # is matter text. Nothing from the child belongs in an exception that is
            # logged, and the prompt itself is no longer on argv to begin with.
            raise TurnError(f"headless turn timed out after {self.timeout_s}s")

        if completed.returncode != 0:
            self._raise_classified(completed.stdout, completed.stderr, completed.returncode)
        return self._parse_result(completed.stdout, key)

    # -- result parsing + failure classification --

    def _parse_result(self, stdout: str, key: str) -> RawTurnResult:
        events = _json_events(stdout)
        if not events:
            raise TurnError("headless turn returned unparseable JSON")
        payload = _final_payload(events)
        if not isinstance(payload, dict):
            raise TurnError("headless turn JSON was not an object")

        # Fail closed BEFORE the text is treated as work product. A turn whose tools were
        # refused still exits 0 with a terminal `is_error: false`, so without this check a
        # persona's apology for not being able to open the vault is returned — and parsed,
        # scored, and filed — as its answer.
        denial = _permission_denial(events, payload)
        if denial is not None:
            raise TurnError(f"headless turn was denied filesystem access: {denial}")
        if payload.get("is_error") is True:
            reported = payload.get("result")
            detail = secrets.redact(reported).strip()[:500] if isinstance(reported, str) else ""
            self._classify(detail.lower())
            raise TurnError(f"headless turn reported an error: {detail}")

        text = payload.get("result")
        if not isinstance(text, str):
            raise TurnError("headless turn JSON had no 'result' text")
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            self._persist_session_id(key, session_id)
        usage = self._usage_from(payload)
        return RawTurnResult(text=_unfence(text), usage=usage, provider_call_id=uuid.uuid4().hex)

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
    def _classify(haystack: str) -> None:
        """Raise the specific failure class if ``haystack`` carries its signature."""
        if any(sig in haystack for sig in _SEAT_SIGNATURES):
            raise SeatLimitError("headless Claude hit a seat/rate limit")
        if any(sig in haystack for sig in _AUTH_SIGNATURES):
            raise AuthError("headless Claude authentication failed")

    @classmethod
    def _raise_classified(cls, stdout: str, stderr: str, returncode: int) -> None:
        cls._classify(f"{stdout}\n{stderr}".lower())
        raise TurnError(
            f"headless turn failed (exit {returncode}): {secrets.redact(stderr).strip()[:500]}"
        )
