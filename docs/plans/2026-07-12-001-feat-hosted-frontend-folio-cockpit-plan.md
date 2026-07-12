---
title: "feat: hosted frontend — FOLIO-grounded cockpit at mootloop.damienriehl.com"
type: feat
status: active
date: 2026-07-12
origin: docs/brainstorms/2026-07-12-frontend-folio-brainstorm.md
---

# ✨ feat: Hosted Frontend — FOLIO-Grounded Cockpit (mootloop.damienriehl.com)

## Overview

MootLoop gains its attorney-facing surface: a **hosted web app** at `mootloop.damienriehl.com` on the Hetzner box, behind **Cloudflare Access**, usable from any device. Six rooms — dashboard, begin-task on-ramps, strategy board, run cockpit, decision inbox, export/audit — over a new authenticated write tier and a **headless Claude Code driver running on Damien's Max plan**. FOLIO grounds everything: the task catalog (Litigation Document, 668 concepts), the strategy skeleton (Litigation Objectives, 1,847), and the audit trail (every LLM proposal snapped to an IRI or flagged `unmapped`).

All 16 brainstorm decisions (F-1..F-16, see origin) carry forward, plus nine plan-stage decisions below (P-30..P-38) resolving what spec-flow analysis surfaced.

## Problem Statement

The pipeline is complete (PRs #1–9) but headless: task initiation, decision resolution, and oversight live in a terminal. The attorney needs remote, visual command — start tasks from a phone, curate a theory-of-the-case map, watch runs live, clear decision queues, attest, export — without ever compromising the vault or paying API rates. Three structural tensions resolved in this plan: hosted privileged data (solved by a layered perimeter), Max-plan economics for a server engine (solved by `claude -p` + queue discipline + optional API failover), and FOLIO's 668-concept catalog vs. one task adapter (solved by on-the-fly task synthesis).

## Plan-Stage Decisions (P-30..P-38, extending the v1 plan's P-27..29)

| # | Decision | Choice |
|---|----------|--------|
| P-30 | Catalog semantics | Every FOLIO concept shows as **"Available"** — the system can build out any concept on the fly. Adapter-backed tasks start immediately; others trigger **task synthesis**: the system composes a declarative adapter config + draft rubric from the FOLIO concept + matter context; the attorney reviews, edits, and locks it; then the run starts. Non-synthesized picks can also seed the board or a research note |
| P-31 | Derived rubrics | Board curation + task synthesis compose base rubric + matter overlay into a **new derived rubric, hash-locked at run start** (lock discipline survives; scoring never changes mid-run). After the run, the rubric is editable again — by the user (single-player now) and the user's team (multiplayer later; schema carries an `edited_by` provenance list from day one) |
| P-32 | Seat-limit behavior | **Pause + notify + resume** by default: run flips to `paused: seat_limit`, ntfy push fires, driver backs off and auto-resumes. The pause notification **offers one-tap API failover** for the run; matter config also supports **auto-failover with an optional budget cap** for urgent tasks. Every failover turn is metered in real dollars against the run budget |
| P-33 | Board feedback loop | Auto-apply run findings to the living board — OC attacks that landed, judge-panel weaknesses (survival distributions on nodes), new facts/gap closures — **with a mandatory system-edit feed**: every automated addition/change appears in a notification feed + board changelog the attorney can review and revert (HOTL: observe, understand, redirect) |
| P-34 | First-matter seeding | Fence-litigation folder seeds via **SSH/rsync** into the server vault (disk-safe, one-time); browser upload serves incremental documents with a disk-quota preflight |
| P-35 | Deploy topology | **Two Coolify apps in the existing `mootloop` project**: the public demo (unchanged, read-only) and the **matter tier** (new Next.js frontend + write-API backend + driver worker), Access-gated, per-hostname mTLS. The demo's `app.py` is never touched (read-only invariant stays green) |
| P-36 | Driver auth path | The driver calls the backend on **localhost only** (never through Access); Cloudflare service tokens exist solely for genuinely external automation. No inbound webhooks at all — watchers poll (keeps the perimeter closed) |
| P-37 | Download policy | Deliverable downloads are permitted (admin=client) via **short-expiry authenticated links, every download logged in the access audit**; DRAFT watermark until attestation, as today |
| P-38 | Watched-file default | Watcher-detected documents land in **needs-triage** (excluded from runs) until the attorney assigns role/privilege — fail-safe, malpractice-aware |

## Technical Approach

### Topology

```
Browser (any device)
  │  Cloudflare Access (Google IdP + 2FA; app session 24h)
  ▼
Cloudflare edge ══ per-hostname Authenticated Origin Pulls (mTLS, this vhost only)
  ▼
Traefik → mootloop-matter-web (Next.js 16, standalone)   ┐
        → mootloop-matter-api (FastAPI write tier)        │ same Coolify project,
                 ▲ localhost only                         │ dedicated OS user,
        mootloop-driver (worker: claude -p engine,        │ matters-root volume
                 watchers, scheduler, notifier)           ┘
Vault: /srv/mootloop-matters/<matter_id>/  (0700, outside all repos)
```

### Security architecture (F-12 — build FIRST, phase FE-0)

- **Perimeter:** Cloudflare Access self-hosted apps for BOTH the UI hostname and the API paths (an Access app on one path does not cover others — first-party lesson). Google IdP, 2FA, 24h app session.
- **Origin validation:** FastAPI dependency verifies `Cf-Access-Jwt-Assertion` on every request — RS256 against `https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` (cached JWKS), pinning `aud` (per-app AUD tag), `iss`, `exp`, and the expected email. Next.js `middleware.ts` mirrors the check with `jose`.
- **Transport lock:** **per-hostname Authenticated Origin Pulls** (own uploaded cert, NOT global AOP) as a Traefik `tls.options` attached only to this vhost — the box's other public apps stay open. Coolify wrinkle: hand-edit Traefik dynamic config (documented in the deploy runbook).
- **App session layer** (defense-in-depth, deliberately thin): CSRF token, per-matter authorization, and **access-audit logging** (who/when/what — every page of matter data, every download). No fallback secrets ever (ontokit-web forged-session lesson); auth-active predicates in one place.
- **Rate limiting in the app** (not just the edge): pure-ASGI middleware (BaseHTTPMiddleware breaks SSE — first-party lesson) on upload + run-start + inference endpoints.
- **Secrets:** all under the dedicated service user (`~mootloop/.mootloop/secrets.env`, 0600): `CLAUDE_CODE_OAUTH_TOKEN` (crown jewel; 1-year; calendar reminder to rotate), Google refresh token (encrypted at rest), ntfy topic, service-token pair. `ANTHROPIC_API_KEY` deliberately absent from driver env unless failover configured (auth-precedence footgun: its presence silently overrides the subscription token).
- **Uploads:** UUID filenames (client filename = metadata only), realpath-containment via `safe_vault_path`, MIME allowlist, size + decompression-ratio limits, zip-path-traversal rejection, content-hash dedup, disk-quota preflight. (The alea-intake upload's path-traversal hole is a known anti-pattern — do not inherit.)
- **Streams:** SSE with 30s heartbeat comments (Cloudflare ~100s idle timeout); client treats redirect-on-reconnect as session-expiry → re-auth prompt; in-progress form state persisted to localStorage.

### The engine (F-4 as researched)

- **One `claude -p` invocation per persona turn**, `--resume <session_id>` for continuity, cwd = the matter directory, `--permission-mode dontAsk`, per-run `--settings` with `deny`/`allow` path rules scoped to that matter — PLUS OS-level enforcement (service user owns only the matters-root; each turn optionally under a per-matter subuser later). `--output-format json` gives `session_id`, usage, `total_cost_usd` for the spend ledger. `DISABLE_AUTOUPDATER=1`, pinned version, `CLAUDE_CONFIG_DIR` explicit, telemetry off.
- **Auth:** `CLAUDE_CODE_OAUTH_TOKEN` (Max plan). Honest posture: CLI automation is sanctioned; *continuous* seat automation is ToS-gray — MootLoop's volume (occasional multi-hour runs, one attorney) sits on the defensible side. The Agent SDK is NOT usable here (requires API key) — the driver shells out to the CLI. `--bare` (warm starts) requires API-key auth, so subscription turns eat full cold starts — acceptable at our concurrency.
- **Single-flight inference queue** (Redis, already on the box): runs, board extrapolations, suggestion generation, and freeform resolution all serialize through it (concurrency 1–2) — one spend ledger, no seat starvation between features, and Damien's interactive use keeps headroom.
- **Seat-limit handling (P-32):** `stream-json` `api_retry`/`rate_limit` events flip the run to `paused: seat_limit` → ntfy push with one-tap failover offer → exponential backoff auto-resume. `authentication_failed` (token expiry) → `needs_attention` + push (silent-stall prevention).
- **Driver = supervised worker service** (the box's first second-service; folio-insights' `Dockerfile.worker` is the template): one `plan_next → assemble_prompt → claude -p → record_turn` tick at a time, never holding `RunLock` across a model call; stale-heartbeat lock takeover on boot; pre-redeploy drain signal.

### Backend extension (`src/mootloop/web/api/` — new package; demo `app.py` untouched)

- **Matter registry:** matters-root convention + `matter_id → vault` resolver; `list_matters`; all routes per-matter. Vault stays outside every repo (existing boundary rules).
- **New domain models** (all `VersionedModel`): **`TaskSpec`** (FOLIO IRI + breadcrumbs + areas facets + UTBMS + source lane wizard/suggestion/freeform + request-set ref + board-curation ref + synthesized-adapter ref) — what all three on-ramps produce and `start_run` consumes (signature gains `task_spec`); **`StrategyBoard`** (nodes: IRI-grounded or `unmapped`, edges, curation flags, element-coverage state, changelog of system edits with provenance); **`SynthesizedAdapter`** (declarative task YAML + derived rubric draft + approval record); **`SuggestionItem`**, **`NotificationEvent`**, **`AccessAuditEntry`**.
- **Write endpoints** (thin wrappers over existing tested services): decide/resolve (typed 409 on lock contention with retry-backoff), attest, run start/continue/raise-cap/failover, upload+tag, requests-parse, board CRUD + extrapolate, task synthesis, suggestions accept/dismiss, connector setup.
- **FOLIO service** (deterministic lane, folio-python): implement folio-mapper's backend contract — `POST /api/folio/candidates`, `GET /api/folio/concept/{iri_hash}/detail`, `GET /api/folio/concept/{iri_hash}/graph` — plus adjacency traversal (alea-intake `adjacency.py` pattern) for the board.
- **Grounding engine:** ConceptResolver cascade (embedding → label → LLM-on-miss, cached by input hash) + resolve-after-generate for all LLM proposals (issue_spot pattern).

### Frontend (`frontend/` in-repo — Next.js 16 App Router, ontokit-web conventions, courtroom-ledger identity)

- Chassis: standalone output, Tailwind v4 CSS-first tokens (`@theme` — port the courtroom-ledger palette; both themes), hand-rolled Radix primitives, TanStack Query v5 **with useMutation + invalidation done properly** (deliberately better than ontokit-web), `cn()` util, Vitest+Testing Library with `mockReactFlow` helper.
- Components copy-adapted from folio-mapper (`transpilePackages` not viable — packages are private/unbuilt): `CandidateTree`, `ConfidenceBadge`, `DetailPanel` (de-hardcode its fetch), `ConceptDAG` (SVG, board-lite), `EntityGraph` + `useELKLayout` (React Flow, the full board canvas; `'use client'`, client-only elkjs import).
- The six rooms as App Router routes; design per the frontend-design discipline (pleading-caption motif carried from the demo viewer; gate chips; persona pipeline strip; distinctive serif/mono pairing; accessibility: keyboard, focus, reduced-motion).
- Build survival on the box: `.dockerignore`, `NODE_OPTIONS=--max-old-space-size=3072`, 3-stage node:22-alpine standalone Dockerfile, HEALTHCHECK; serialized deploys (`concurrent_builds=1` verified), disk preflight in the deploy runbook.

### Watchers, suggestions, notifications (driver-hosted)

- **Google Drive polling** every 2–5 min: `changes.list(startPageToken)` (token durable across restarts), filter to connected folders, new files → needs-triage + suggestion + push. OAuth app **"In Production/unverified"** (else refresh tokens die in 7 days — Damien clicks through the one-time unverified screen); scopes `drive.readonly` + `gmail.send`; OAuth connect via a narrow Access-bypass callback path OR device flow (decide in FE-5 by testing; prefer device flow = zero perimeter holes). Dropbox/OneDrive: same cursor+timer pattern, post-v1.
- **Suggestion engine:** event-driven (ingest, run-finished, deadline horizon via the alea-intake deadline engine — pure, cited, MN+federal) + on-open compute; all LLM-backed suggestion generation rides the inference queue.
- **Notifications:** ntfy.sh (long random topic; **content-free payloads** — "Matter A: 2 items need decisions" + deep link; Access preserves the original URL through login) + Gmail-API daily digest (content-light: counts, deadlines, spend, links). Per-matter mute + quiet hours.

## Implementation Phases

> Sessions are focused half-days. Security lands first; each room ships usable.

- **FE-0 — Security foundation + hosted skeleton (4 sessions):** threat-model doc (`docs/security-frontend.md`); Cloudflare Access apps + policies (+ service token); DNS `mootloop.damienriehl.com` (zone is on Damien's Cloudflare — automatable); per-hostname AOP cert + Traefik tls.options; matter registry + matters-root + service user; `web/api/` scaffold with Access-JWT dependency + audit log + rate limiting + CSRF; Coolify matter-tier apps (web/api/driver, `instant_deploy=false`); secrets provisioning. **Gate: penetration checklist passes (direct-origin blocked, JWT forgery rejected, path traversal, rate limits) before any matter data touches the server.**
- **FE-1 — Engine + run lifecycle (3 sessions):** driver worker (queue consumer, per-turn sandbox, spend ledger, seat-limit pause/notify/resume + failover per P-32, lock discipline, drain/reclaim); run APIs; SSE journal streaming with heartbeats. Gate: full synthetic run driven end-to-end on the server via real `claude -p` (one live smoke run — mock-green is a false signal).
- **FE-2 — Cockpit + decision inbox (3 sessions):** Next.js chassis + the two rooms fronting existing primitives; decide/attest flows; run controls (start/pause/continue/raise-cap/failover). Gate: a phone-driven run of the synthetic matter start→decide→attest→export.
- **FE-3 — On-ramps + task synthesis (3 sessions):** FOLIO catalog service + wizard (search-first tree, facet chips, "Available" semantics per P-30); TaskSpec; freeform lane with resolve-after-generate; task-synthesis flow (adapter YAML + derived rubric draft → attorney review/lock per P-31); suggestion surfacing (accept → TaskSpec).
- **FE-4 — Strategy board (4 sessions):** StrategyBoard model + CRUD; extrapolation jobs (queue-metered); React Flow board (claims×defenses×elements axes, adjacency DAG, coverage coloring); curation → prompt-injection artifact + gap targets + derived-rubric overlay; run-findings auto-apply + system-edit feed/changelog per P-33.
- **FE-5 — Ingestion + watchers (3 sessions):** hardened upload + tagging UI + needs-triage; Drive connector (OAuth dance, polling watcher); deadline scheduler; suggestion events.
- **FE-6 — Dashboard + audit room + notifications (2 sessions):** matter dashboard; export/audit room (per-passage attribution, citation ledger, download links per P-37); ntfy + digest + mute controls.
- **FE-7 — Hardening validation + live cutover (2 sessions):** security regression suite; fence-litigation seeding via SSH (P-34); Google OAuth production-status setup; first live hosted run with the real matter; runbook + README/AGENTS updates.

## System-Wide Impact

- **Interaction graph:** UI → write-API → services (existing) → journal → SSE → UI; driver polls queue → orchestrator tick → journal; watchers → suggestions → notifications → deep links → UI. Board curation → spawn-time injection artifact (existing learnings read-back channel — no persona forks).
- **Error propagation:** seat-limit and auth-failure events become typed run states + pushes (never silent stalls); lock contention = typed 409 + client retry; every gate stays fail-closed; Access session expiry = client re-auth flow, not data loss.
- **State lifecycle:** all new stores append-only or versioned (TaskSpec, board changelog, audit log) per the house discipline; board edits never touch attested-master hashes (attestation tuple unchanged); derived rubrics get their own locked version files per run.
- **API surface parity:** every UI action maps to a documented endpoint; CLI parity for the new verbs (`mootloop matters list`, `board show`, `tasks synthesize`…) tracked in the capability map; the driver uses the same service functions the CLI does.
- **Integration tests that matter:** direct-to-origin request rejected (mTLS + JWT); synthetic run driven by the real driver with an injected seat-limit → pause → resume; task synthesis → locked derived rubric → run start; board auto-apply → changelog + notification; upload traversal/zip-bomb rejection; watcher detects a planted Drive file → needs-triage → never enters a run untriaged.

## Acceptance Criteria (condensed)

- [ ] Direct-to-origin and cross-app JWT requests rejected; all matter routes require valid Access JWT (aud/iss/email pinned); every access + download audit-logged
- [ ] Full run start→finish driven by the hosted engine on the Max token, streamed live to a phone, with decisions resolved and attestation recorded from the browser
- [ ] Seat-limit mid-run → pause + push + auto-resume; optional API failover honors budget caps and meters real dollars
- [ ] All three on-ramps produce TaskSpecs; a non-adapter FOLIO concept synthesizes an adapter + derived rubric that locks at run start
- [ ] Strategy board: FOLIO-grounded + unmapped nodes, curation drives prompts/gaps/rubric overlay, run findings auto-apply with a reviewable system-edit feed
- [ ] Drive watcher detects new files → needs-triage → suggestion + push; nothing untriaged enters a run
- [ ] Demo tier untouched (read-only invariant green); vault boundary + privacy-grep + canary architecture intact; no secrets in repo/images
- [ ] make check green throughout; frontend tests + security regression suite in CI

## Dependencies & Risks

- **Damien actions:** Cloudflare Access team setup approval (I can API-drive most of it), Google Cloud OAuth app → "In Production" + one-time unverified-screen click-through, `claude setup-token` on the box, fence-folder rsync, mootloop.org DNS (still pending from the deploy plan).
- **Risks:** ToS-gray headless Max usage (mitigated: low volume, CLI-only, API failover switch ready); box capacity (8GB/85% disk — lean images, no torch, prune discipline; if it tightens, the dedicated-matter-box option from the brainstorm reopens); Coolify/Traefik hand-config for AOP (budgeted); first-ever worker service on this box (template exists, folio-insights).
- **Deferred:** Dropbox/OneDrive connectors, Web Push, multiplayer rubric editing (schema ready), UTBMS billing integration, generic fallback adapter.

## Sources & References

- **Origin brainstorm:** [docs/brainstorms/2026-07-12-frontend-folio-brainstorm.md](../brainstorms/2026-07-12-frontend-folio-brainstorm.md) — F-1..F-16 carried in full
- **v1 plan:** docs/plans/2026-07-11-001 (§D3 security, §D9 lifecycle, §D11 parity vocabulary); deployment plan docs/plans/2026-07-11-002
- **Internal:** orchestrator stepwise API (`src/mootloop/orchestrator.py:171,210,242,256`); write services (`decisions.py:230`, `attest.py:110`, `ingest.py`); read-only invariant (`tests/invariants/test_web_readonly.py`); chassis (`ontokit-web/{auth.ts,lib/api/client.ts,Dockerfile}`); components (`folio-mapper/packages/ui/src/components/mapping/*`); grounding (`alea-intake/backend/app/services/folio/{concept_resolver,adjacency}.py`, `analysis/stages/issue_spot.py`); deadlines (`alea-intake/backend/app/services/deadline/`); worker template (`folio-insights/Dockerfile.worker`)
- **First-party learnings:** CF-Access JWT/aud (`websites/docs/solutions/cloudflare-access-jwt-worker-verification.md`); build OOM + healthcheck race (`docs/solutions/2026-07-06-coolify-caddy-traefik-cutover.md`); containerd bloat + queue wedge; forged-session (`ontokit-web/docs/solutions/security-issues/`); ASGI rate limiting (`folio-api/docs/solutions/security-issues/`); fqdn/TLS recipe (`websites/docs/solutions/coolify-compose-service-fqdn.md`)
- **External:** Cloudflare Access JWT validation + FastAPI tutorial + per-hostname AOP + service tokens (developers.cloudflare.com); Claude Code headless/auth/permissions (code.claude.com/docs); Drive changes.list + OAuth publishing status (developers.google.com); ntfy (docs.ntfy.sh); Dropbox/Graph delta docs
- **Spec-flow:** 24 findings — C1..C10 resolved via P-30..P-38 + architecture; E1..E14 + N1..N10 adopted as stated resolutions in the phases above
