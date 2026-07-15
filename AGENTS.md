# MootLoop — Agent Instructions

Agentic law firm simulator. Six personas draft, attack, and adjudicate legal work
product through rubric-gated loops, with a human attorney on the loop. This file is
the single source of agent instructions; `CLAUDE.md` is a symlink to it.

## Setup

```bash
make setup      # uv sync + pre-commit install
```

Requires Python 3.12 (`.python-version`) and [uv](https://docs.astral.sh/uv/).
All commands run through `uv run`.

## Commands

```bash
make lint        # ruff check --fix
make format      # ruff format
make typecheck   # mypy --strict (authoritative gate)
make test        # pytest with coverage
make check       # lint + typecheck + test (run before every commit)
```

Run the CLI with `uv run mootloop --help`.

## Architecture

- `src/mootloop/` — src-layout package.
  - `models/` — all domain types live here to prevent circular imports.
    - `common.py` — `NewType` IDs, `MatterText`/`PublicText`, `VersionedModel` base.
    - `matter.py` — `MatterConfig` schema for `matter.yaml`.
  - `vault.py` — the matter-vault module: path hardening, sync-folder detection,
    matter load/create, and the per-matter run lock. **All vault writes go through
    `safe_vault_path`** — the single realpath-containment choke-point.
  - `privacy.py` — canary tokens + `privacy_grep` (fails closed).
  - `errors.py` — exception hierarchy (`MootloopError` base).
  - `cli.py` — Typer app; commands are thin (~3-line) adapters over service functions.
- `tools/` — thin script entry points (e.g. `privacy_grep.py`) invoked by pre-commit/CI.
- `tests/{unit,invariants}/` — unit tests + repo-hygiene / structural invariants.

## Key Patterns

- **Domain types own the vocabulary.** `NewType` IDs (`MatterId`, `RunId`, `FactId`,
  …) are distinct types; every persisted model extends `VersionedModel`
  (`extra="forbid"` + `schema_version`), so unknown fields become field-named
  validation errors for free.
- **Fail closed.** Every confidentiality/privacy control errs toward blocking:
  unreadable/symlink/binary files in `privacy_grep` are findings, not skips.
- **Sync vs. async.** Keep the core sync; only future HTTP-client and orchestrator
  fan-out layers are async, fronted by `anyio.run` facades.
- **Minimal comments.** Docstrings only where behavior isn't obvious.

## Vault-boundary rules (non-negotiable)

- **Matter data NEVER lives in the repo.** Vaults live outside the repo tree;
  `assert_vault_outside_repo` enforces `realpath(vault) ⊄ realpath(repo)` and vice
  versa at run start. `matters/` is gitignored as a backstop, not the mechanism.
- **Secrets only in `~/.mootloop/secrets.env` or the OS keychain** — never in
  `matter.yaml`, config, or the vault. Personas run with minimal env; only the
  deterministic core holds credentials.
- **Active vaults never live in background-sync folders** (Dropbox, Google Drive,
  iCloud, OneDrive). Preflight detects sync markers and warns/blocks; the supported
  backup path is an idle-only, lock-checked snapshot.
- **Matter/run IDs are validated** (`^[a-z0-9][a-z0-9._-]{0,63}$`); `.`, `..`, and
  path separators are rejected. Ingested filenames are metadata, never output paths.
- **Matter-ID convention (Damien's 2026-07-15 ruling — supersedes "opaque, never
  party-derived"):** `matter_id = YYYY-MM-DD-<client>-<descriptor>`, where the date is
  the **first client contact** with the lawyer — so the matter ID itself sorts as the
  first item on the case timeline. Owner/attorney/client's explicit call: readable,
  timeline-leading IDs beat opacity here. Still bound by the regex above (lowercase,
  no path separators). Example: `2025-10-16-riehl-fence`.

## Code Quality Settings

- **ruff**: line-length 100; lint select `E,W,F,I,B,C4,UP,SIM`.
- **mypy**: `strict = true` with the pydantic plugin — the authoritative type gate.
- **pytest**: `--strict-markers`; tests split into `unit/` and `invariants/`.
- `make check` must be green before every commit.
