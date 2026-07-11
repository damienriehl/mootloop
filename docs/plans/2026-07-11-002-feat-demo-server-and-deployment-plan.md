---
title: "feat: MootLoop demo server + deployment — public read-only demo tier"
type: feat
status: active
date: 2026-07-11
origin: docs/plans/2026-07-11-001-feat-mootloop-v1-agentic-litigation-pipeline-plan.md §D13
---

# ✨ feat: MootLoop Demo Server + Deployment

## Overview

A public, read-only demo tier that shows the full agentic-law-firm arc — six
personas drafting, attacking, and adjudicating discovery responses through
rubric-gated loops — on the **synthetic Northfield Widgets matter only**, at:

- **DEV:** `mootloop.dev.openlegalstandard.org`
- **PROD:** `mootloop.org` (later; ask-gated deploys)

The demo run is **pre-baked at image build time** with the `FakeLLMProvider`:
zero LLM calls at runtime, zero secrets at runtime, every response derived from
vault files via the existing fold/reader functions.

## Hard rules

1. **Servers never host real matter data.** The demo server has *zero*
   matter-data mechanisms: it never reads `~/Matters`, accepts no uploads, and
   the only vault it can see is the synthetic one baked into the image. The
   repo's vault-boundary rules (AGENTS.md) apply structurally — the web app
   imports no write-capable functions (enforced by an invariant test).
2. **No secrets required at runtime.** The container runs with an empty env
   (only `MOOTLOOP_DEMO_VAULT`, defaulted).
3. **Everything read-only.** `bake.py` is the only writer and is never imported
   by `app.py`.
4. **No LLM calls at runtime.** The run is deterministic and pre-baked.

## Captured insights

### (a) Purpose

Public demo tier proving the article's thesis visibly: iterated, adversarially
tested, panel-adjudicated work product — the full pipeline (draft → partner
critique → OC attack → bolster → judge panel → restructure → rubric gate →
decisions → attest → export) browsable end-to-end on synthetic data.

### (b) House Coolify recipe (as verified on the Hetzner dev box)

- One app per repo, built from **its own Dockerfile** (dockerfile buildpack).
- Port **8000**, `/health` endpoint, baked `HEALTHCHECK` (curl pattern).
- Env vars live **only in Coolify** — never in the repo.
- **Serial builds**: `concurrent_builds=1`; concurrent builds have OOM'd the
  box — caution.
- Disk was ~91% full at last check — **prune before builds**
  (`docker system prune -af`).
- Coolify API token at `~/.coolify-token` on the box.
- Server UUID: `azqkiidl028fi9yqbf7wg7nc`.

### (c) Finding: GitHub App coverage gap

The Coolify GitHub App (`coolify-alea-dev`) covers only the `alea-institute`
org, **not** `damienriehl/mootloop`. Auto-deploy therefore uses the
**public-repo app** path plus a **plain GitHub webhook** pointed at Coolify's
manual webhook endpoint (per-app webhook URL + secret configured in Coolify).

### (d) Finding: Docker / vault-boundary interaction

`init_vault` runs `assert_vault_outside_repo` only when an enclosing `.git`
work-tree is found (`enclosing_git_repo`). `.dockerignore` excludes `.git`, so
the in-image bake to `/app/demo-vault` (inside the copied repo tree) passes the
boundary preflight — legitimately: the baked vault is synthetic fixture data,
not matter data. Locally and in tests, bakes always target paths outside the
repo (`/tmp/...`, pytest `tmp_path`), where the assertion stays fully armed.

### (e) Clarification: Max plan vs. API

The fence litigation runs **locally** via Claude Code on Damien's Claude Max
plan: Fable orchestrates; Opus persona subagents execute turns on plan quota —
**zero API cost**. The only credential needed is a free CourtListener token for
citation verification. Budget figures shown in the demo/CLI are *notional*
(plan-quota accounting, not billed dollars). `mootloop.org` is never used for
real matters — it serves the pre-baked synthetic demo only.

### (f) Build plan (4 units, one commit each) + deployment steps

**UNIT 1 — `feat(web-api)`: demo vault baking + read-only API**

- `[project.optional-dependencies] web = [fastapi, uvicorn]`.
- `src/mootloop/web/bake.py` — `build_demo_vault(dest)`: init vault from
  `fixtures/synthetic-matter/matter.yaml`; ingest source-docs with tags; parse
  all three served sets (rogs/rfps/rfas); add facts; drive the full pipeline
  with a scripted `FakeLLMProvider` (low-survival judge on a subset of requests
  → the restructure pass triggers; RFA drafts → RFA-disposition decisions);
  resolve all decisions (decided_by "Demo Attorney", source human); curate the
  planted authority and verify citations; attest (reviewer "Demo Attorney");
  export (markdown always; DOCX iff pandoc). Deterministic: fixed `now`
  timestamps, fixed run id. CLI: `mootloop web bake <dest>`.
- `src/mootloop/web/app.py` — read-only FastAPI over the baked vault
  (`MOOTLOOP_DEMO_VAULT`, default `/app/demo-vault`): `/health`,
  `/api/matter`, `/api/run`, `/api/requests`, `/api/requests/{id}/turns`,
  `/api/requests/{id}/panel`, `/api/decisions`, `/api/gates`,
  `/api/deliverables`, `/api/deliverables/{name}` — all derived from vault
  files via existing fold/reader functions; no new state.

**UNIT 2 — `feat(viewer)`: the run viewer**

Single-page app at `/` (static HTML+CSS+JS served by FastAPI, no build step,
no CDN). Design direction **"courtroom ledger"**: cool paper `#F7F8F6` / ink
`#1D2226`, oxblood accent `#7C3030` (dark: `#16191C` ground, `#C4706E`
accent); Charter/Cambria serif prose; `ui-monospace` eyebrows/IDs/data;
light + dark via `prefers-color-scheme` + `data-theme` override. Layout:
pleading-caption header, six-persona pipeline strip with turn counts, request
table with per-request gate chips, request detail (iteration timeline,
expandable turns, objection-survival bars, final response), decisions drawer,
deliverables tab rendering markdown, footer with citator disclosure +
"synthetic demonstration matter — no real client data" + GitHub link.
Accessible: keyboard navigation, visible focus, `prefers-reduced-motion`,
scrolling table containers.

**UNIT 3 — `feat(docker)`: Dockerfile + deploy scaffolding**

`python:3.12-slim`; apt pandoc + curl; uv with the `web` extra; COPY repo;
`RUN mootloop web bake /app/demo-vault` at **build** time; non-root user;
`EXPOSE 8000`; `CMD uvicorn mootloop.web.app:app --host 0.0.0.0 --port
${PORT:-8000}`; `HEALTHCHECK` curl `/health` (alea-intake pattern).
`.dockerignore`: `.git`, `.venv`, `__pycache__`, `tests`. `docs/deploy.md`
records the recipe below.

**UNIT 4 — `test(web)`: tests + docs**

Bake integration test (finished + attested vault, restructure present, RFA
decisions resolved); API tests over a session-scoped baked-vault fixture
(health, run, requests shape, turns/panel, deliverable fetch, 404s);
path-traversal on deliverable name → fail closed; read-only invariant test
(`app.py` imports no write-capable functions; `bake.py` never imported by
`app.py`); README Demo section.

**Deployment steps**

1. Coolify project **`mootloop`** on the dev box.
2. **DEV app:** public repo `https://github.com/damienriehl/mootloop`,
   dockerfile buildpack, port 8000, domain
   `mootloop.dev.openlegalstandard.org`, branch `main`, auto-deploy via plain
   GitHub webhook → Coolify manual webhook endpoint (see insight (c)).
3. **PROD app:** domain `mootloop.org`, **manual, ask-gated deploys** (house
   rule: prod deploys always ask).
4. **Cloudflare DNS:** A records for both domains → the box, **DNS-only**
   (grey cloud) so Coolify's Let's Encrypt flow issues certs directly.
5. Before builds: prune docker (disk ~91%); builds run serially.

## Acceptance criteria

- `make check` green (ruff, mypy --strict including `mootloop.web`, all tests).
- Bake produces a finished, attested, export-ready vault with ≥1 restructure
  turn and resolved RFA decisions — deterministically.
- The API serves everything the viewer needs from vault files only.
- Docker image builds with the vault baked in; `/health` returns ok with no
  env and no secrets.
- Read-only invariant test enforces the no-writer rule on `app.py`.

## Sources

- Origin: `docs/plans/2026-07-11-001-feat-mootloop-v1-agentic-litigation-pipeline-plan.md` §D13
  (public demo tier decision).
- House Dockerfile patterns: `folio-enrich/Dockerfile`, `alea-intake/Dockerfile`.
- Coolify recipe: verified on the Hetzner dev box (server UUID
  `azqkiidl028fi9yqbf7wg7nc`, token at `~/.coolify-token`).
