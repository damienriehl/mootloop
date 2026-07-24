# Third-Party Components

Every external component MootLoop uses (or plans to use) is logged here with its
license and integration mode. **Mode** is one of:

- `dependency-planned` — will be pulled in as a package dependency.
- `copy-planned` — source will be copied/adapted into this repo (attribution + license
  retained per the component's terms).
- `dependency` / `copy` — currently integrated.

| Component | Source | License | Mode |
|-----------|--------|---------|------|
| pydantic | https://github.com/pydantic/pydantic | MIT | dependency (models everywhere) |
| typer | https://github.com/fastapi/typer | MIT | dependency (the `mootloop` CLI) |
| PyYAML | https://github.com/yaml/pyyaml | MIT | dependency (`matter.yaml`, rubrics, personas) |
| cryptography | https://github.com/pyca/cryptography | Apache-2.0 OR BSD-3-Clause | dependency (encrypted backups, download-link signing) |
| PyJWT | https://github.com/jpadilla/pyjwt | MIT | dependency (Cloudflare Access JWT verification) |
| FastAPI | https://github.com/fastapi/fastapi | MIT | dependency (`web` extra — demo + matter API) |
| uvicorn | https://github.com/encode/uvicorn | BSD-3-Clause | dependency (`web` extra — ASGI server) |
| hatchling | https://github.com/pypa/hatch | MIT | build backend |
| pytest, pytest-cov, mypy, ruff, types-PyYAML | — | MIT | dependency (dev) |
| python-docx | https://github.com/python-openxml/python-docx | MIT | dependency (Phase 1 — `.docx` normalization) |
| folio-python | https://github.com/alea-institute/folio-python | MIT | dependency-planned |
| alea-intake `ConvergenceEvaluator` structure | alea-intake `backend/app/services/analysis/convergence.py` @ `18d8cf5` (+ `ConvergenceSignals`/`ConvergenceWeights` in `schemas.py`) | MIT | copy (Phase 3 — **structure only**, re-mapped for drafting) |
| alea-intake components (scoring, DOCX export) | https://github.com/alea-institute (alea-intake) | MIT | copy-planned (Phase 7) |
| FreeLawProject eyecite | https://github.com/freelawproject/eyecite | BSD-2-Clause | dependency (Phase 4 — local citation extraction) |
| httpx | https://github.com/encode/httpx | BSD-3-Clause | dependency (Phase 4 — the sole HTTP-client layer) |
| respx | https://github.com/lundberg/respx | BSD-3-Clause | dependency (dev — mocks httpx in citation tests; no live network) |
| CourtListener v4 API | https://www.courtlistener.com/help/api/rest/ | data via free token | external service (Phase 4 — citation-lookup verification) |
| MN Revisor (statutes / court rules) | https://www.revisor.mn.gov/ | public stable-URL pages | external service (Phase 4 — no API; stable-URL scrape) |

## Frontend (`frontend/package.json` — Next.js matter cockpit)

| Component | License | Notes |
|-----------|---------|-------|
| next, eslint-config-next | MIT | App Router (Next 16) |
| react, react-dom | MIT | |
| @tanstack/react-query | MIT | server-state cache |
| zustand, clsx, tailwind-merge | MIT | client state + class utilities |
| zod | MIT | runtime schema validation |
| jose | MIT | Cloudflare Access JWT verification in middleware |
| openapi-fetch, openapi-typescript | MIT | typed client generated from `openapi.json` |
| @microsoft/fetch-event-source | MIT | SSE run stream with auth headers |
| tailwindcss, @tailwindcss/postcss, postcss | MIT | styling |
| typescript, eslint | Apache-2.0 / MIT | dev tooling |
| vitest, jsdom, msw, @testing-library/* | MIT | dev/test only |

## External binaries invoked as subprocesses (not linked, not redistributed)

| Tool | License | Notes |
|------|---------|-------|
| **pandoc** | **GPL-2.0-or-later** | `export/docx_render.py` shells out to the pandoc CLI (subprocess, no shell) to render DOCX from court-formatted markdown, and degrades gracefully when it is absent. MootLoop does not link against, modify, or vendor pandoc source, so no GPL obligation attaches to this MIT codebase. **Note:** `Dockerfile`, `Dockerfile.driver`, and `Dockerfile.matter-api` install the distro `pandoc` package, so those images contain an unmodified GPL binary alongside MIT code (mere aggregation). That is fine for images we deploy ourselves; if a MootLoop image is ever redistributed to third parties, the GPL's source-availability obligation for pandoc travels with it. |
| **Claude Code CLI (`claude -p`)** | proprietary (Anthropic) | `engine/claude_provider.py` runs each persona turn as a sandboxed subprocess. Not redistributed; the operator supplies their own subscription or API credential. |

### Copy note — `ConvergenceEvaluator` (Phase 3)

`src/mootloop/convergence.py` copies the **weighted-signal structure** of alea-intake's
`ConvergenceEvaluator` (pinned commit `18d8cf5`) — the evaluator/signals/weights
shape — under its MIT license. Per plan D1, the signals are **re-mapped for drafting**:
alea's intake signals (`user_fatigue`, intake `coverage`, `diminishing_gaps`,
`confidence_plateau`) do **not** carry over. `user_fatigue` is dropped; the loop rule
is an explicit AND of three floors (score-delta / material-change / coverage) rather
than a weighted-threshold vote. No alea-intake code is imported at runtime.

Update this table in every ship that adds, removes, or changes a third-party
component, and comply with each component's license (retain MIT/BSD/CC-BY notices
and attribution).
