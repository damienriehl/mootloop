# Plan Completion Audit — 2026-08-20

## Result

Before this audit began, no plan artifact had been created or changed during the strict 21-day window from
2026-07-30 through 2026-08-20 (America/Chicago). The interruption did not leave an
uncommitted code change: the only pre-existing dirty file was the timer-managed
`.claude/RESUME.md`. Both recent feature PRs are merged, and the backend and frontend
validation suites are green.

The broader repository audit nevertheless found genuine unfinished work in the two
legacy plans still marked `active`. This document is the completion ledger for every
plan unit and acceptance criterion. Open autonomous work is routed to
`docs/plans/2026-08-20-0845-feat-plan-completion-queue-plan.md`; operations requiring
attorney judgment, privileged matter access, OAuth consent, or production changes are
routed to `docs/decisions/2026-08-20-plan-completion-decision-sheet.md`.

## Audit Method

- Window: 2026-07-30 00:00 through 2026-08-20 23:59, America/Chicago.
- Searched the working tree, all Git refs, reflogs, and unreachable commits for plan
  additions or changes. Result: zero plan-changing commits in the window.
- Audited all three pre-existing source plans because the user asked for all planned
  work, not only files whose timestamps happened to fall in the window. The fourth
  current plan file is the August 20 continuation queue created by this audit.
- Compared each task with current source, tests, documentation, Git history, merged
  PRs, and the two recent operational records.
- Did not read or mutate hosted matter data. Any conclusion requiring that access is
  intentionally `DECISION-GATED`, not guessed.

### All-history discovery extension

The follow-up audit removed the 21-day cutoff entirely. It searched the current
workspace, every branch and tag, 4,038 commits reachable through refs/reflogs, 4,664
recoverable unreachable commits, deleted/renamed plan paths, and plan-signature blobs.
No fourth historical `ce-plan` artifact was found. The three July 11–12 source plans
below are therefore the complete original plan corpus recoverable from this repository.
The August 20 continuation plan is a derivative queue created by this audit, not a
missing older source plan.

## Current Validation Evidence

- Backend verification: the final `make check` passed: ruff clean, mypy strict clean
  across 85 source files, and 737 tests passed at 92% coverage.
- Frontend verification: ESLint clean, TypeScript clean, 9 Vitest files / 37 tests
  passed, backend and generated-client OpenAPI drift checks pass, and the production
  build succeeds.
- GitHub PR #30, execution/evidence/export hardening, merged 2026-08-06.
- GitHub PR #31, first-class reopen recovery, merged 2026-08-06.
- No GitHub issue was created or updated in the strict window.
- At audit start, the current feature branch had no unique code commit relative to
  its already-merged result and its file tree matched `origin/main`. U-00 now adds the
  documented CI, invariant, and README corrections published in PR #32.

Status vocabulary: `COMPLETE`, `PARTIAL`, `OPEN-AUTO`, `DECISION-GATED`, or
`DEFERRED`.

### Literal checklist reconciliation

Checkboxes are evidence hints, not the final verdict: several were never updated after
implementation, and several hosted acceptance rows duplicate the same missing feature.

| Source plan | Literal checked | Literal unchecked | Reconciled result |
|---|---:|---:|---|
| V1 litigation pipeline | 12 | 11 | Three unchecked rows are stale-complete; eight remain partial/open/gated and are mapped below. |
| Demo/deployment | 0 | 0 | All five prose acceptance criteria are complete. |
| Hosted FOLIO cockpit | 17 | 14 | Seven unchecked rows are stale-complete; seven remain partial/open/gated, with duplicate live/failover rows consolidated below. |

The audit also treats every D1–D13 and FD-1–FD-10 deepening amendment as normative.
Their rollups appear below; every individual bullet, control, threshold, named verb,
and acceptance gate—165 atomic commitments in total—has a stable disposition in
`docs/audits/2026-08-20-plan-atomic-commitment-ledger.md`. Referenced brainstorms are
not separate `ce-plan` artifacts, but every commitment the plans explicitly carried
forward is covered by the amended phase or deepening row that adopted it.

## Plan 1 — MootLoop v1 Agentic Litigation Pipeline

Source: `docs/plans/2026-07-11-001-feat-mootloop-v1-agentic-litigation-pipeline-plan.md`
(`status: active`).

### Phase ledger

| ID | Planned task | Status | Evidence or queue |
|---|---|---|---|
| V1-0 | Scaffold, vault schema/lock, privacy guardrails, synthetic fixture, CLI init | COMPLETE | Current package, vault hardening, privacy tools, fixtures, and tests implement the phase. |
| V1-1a | Folder ingest, normalization, manifest, failure surfacing, role/privilege tags | PARTIAL | Generic ingest exists and fails closed. The historical real-folder run left 240 conversion items and one oversized item; private-folder completion is D-04. |
| V1-1b | Parse interrogatories/RFPs/RFAs into per-request items | COMPLETE | Parsers and unit/invariant coverage exist. |
| V1-1c | Fact repository, provenance, and gap questions | COMPLETE locally / DECISION-GATED live | Models and services exist; the prior hosted run loaded zero client facts. Live fact supply and validation are D-02/D-03. |
| V1-2a | Six personas, discovery adapter, defaults, thin full pipeline | COMPLETE | Persona/stage/orchestrator code and synthetic pipeline tests exist. |
| V1-2b | Journaled crash resume, idempotent turns, derailment detection, non-degeneracy | COMPLETE for current pipeline | Runs replay an exact approved launch snapshot, fail closed on missing/tampered context, and repair journaled hosted launch delivery idempotently. |
| V1-2c | Individually enable/bypass personas and select thin-full/deep-core/adversarial-first | OPEN-AUTO | Thin-full exists; full controls/strategies do not. U-06. |
| V1-3 | Convergence, locked/versioned rubric, rubric judges, estimates/metering/hard cap | COMPLETE | Implemented and covered; the unchecked rubric/check-suite boxes are stale. |
| V1-4 | Citation clients/cache, fabrication gate, and research-request queue | COMPLETE for current routes | Gate framework and current legal-source clients exist. OpenLaws consolidation remains intentionally deferred. |
| V1-5 | Decisions, gated/observed/autonomous modes, configurable attorney gates | COMPLETE | Durable decision and attestation primitives exist across CLI/API for current actions. |
| V1-6a | Discovery judge panel and costed restructure pass | COMPLETE | Implemented and exercised by synthetic/demo tests. |
| V1-6b | Optional jury panel and calibrated-judge builder | PARTIAL / DEFERRED | Generic panel support exists; assigned-judge corpus builder remains post-first-serve work. U-07. |
| V1-7a | Stable anchors, DOCX, memo, privilege log, attestation, audit, watermark/residue gate | COMPLETE | Current export and attestation modules/tests cover the core court-usable lane. |
| V1-7b | Google Doc export, annotated draft, Google comment reimport | DECISION-GATED | Explicitly deferred by D13; local DOCX learning should be proven first. D-06 and U-09. |
| V1-8 | Edited DOCX/GDoc reimport, anchored diffs, reviewed tier routing, scrubbed learning promotion/readback | OPEN-AUTO, with external lane gated | No edit-learning service exists. Local DOCX/core lifecycle is U-09; Google access is D-06. |
| V1-9a | Hidden answer-key persona regression oracle in CI | OPEN-AUTO | No answer-key oracle exists. U-10. |
| V1-9b | Hand-draft benchmark, judged live run, first compounding-loop cycle | DECISION-GATED | Requires attorney benchmark verdict and private matter access. D-01 through D-03. |
| V1-X1 | RFP responsive/non-responsive light-production suggestions | OPEN-AUTO | No suggestion artifact/service exists. U-08. |
| V1-X2 | `folio-enrich` localhost-only ingest lane, allowlist preflight, no matter web search | DECISION-GATED | Boundary primitives exist, but the planned integration is absent. Product direction is D-05; provider-neutral implementation is U-04B. |
| V1-X3 | Full CLI breadth and non-discovery adapters | DEFERRED | Explicit post-first-serve scope. CLI parity foundations are U-11A/U-11B; adapter breadth remains in the Deferred Follow-up Queue. |
| V1-X4 | README, AGENTS, THIRD-PARTY, mypy, ruff, pytest/invariants | COMPLETE | Files are present and both current validation suites pass. |
| V1-DOCS | Ethics/supervision guidance and durable solution learnings | PARTIAL | `docs/ethics.md` is absent; plugin/skill and operational documentation closure is queued in U-11A. Solutions remain event-driven as lessons are completed. |

### Acceptance-criterion reconciliation

The original unchecked boxes for `make check`, rubric locking/versioning, and current
documentation are mostly stale; `README.md` itself still understates hosted-provider
wiring and is corrected in U-00. The combined export box is `PARTIAL`: DOCX,
memo, privilege log, audit, attestation, watermark, and residue protection are done;
GDoc and annotated-draft lanes remain deferred. Crash resume now fails closed against
drift in the snapshotted launch sources; approved context inputs and hosted launch
delivery now share the same immutable commitment and recovery boundary.

### Exhaustive D1–D13 deepening disposition

| ID | Reconciled status | Evidence or queue |
|---|---|---|
| D1 Architecture | PARTIAL | Provider/task/stage protocols, deterministic convergence, and the gate ledger exist. Uniform gate order/dependencies, copied-component seams/provenance, and remaining layering invariants are U-05. |
| D2 Packaging and skills | PARTIAL | Six personas and two local skills exist. Namespaced plugin packaging, complete side-effect invocation guards, compact navigators, and skill/CLI breadth are U-06/U-11A/U-11B. |
| D3 Adversarial security | PARTIAL | Path/vault/privacy/redaction/type controls exist. Enforced egress and isolation are U-02; complete integrity is U-03; hostile reimport/learning and Google ACL controls are U-09/D-06. |
| D4 Performance and scale | OPEN-AUTO | Flat-context proof, bounded fan-out, prompt caching, objection batching, cache metrics, and calibrated retry/concurrency are U-16. The process-wide CourtListener bucket already exists. |
| D5 Cost and budget | PARTIAL | Tiered models, dated pricing, cache-aware metering, estimates, labels, and caps exist. Output-cap, batching, cache, and self-calibration refinements remain U-16. |
| D6 Loop calibration | PARTIAL | Score delta + material-change + completeness convergence is implemented. Jury-directionality and assigned-judge calibration constraints land with the optional panels in U-07. |
| D7 Discovery-practice gates | PARTIAL | Current discovery shapes, RFA decisions, rubric, privilege log, and structural export are implemented. Opinion-to-proposition support and remaining panel/legal-source work are U-07. |
| D8 Export round trip | PARTIAL | DOCX rendering and attested local exports exist. Bookmark/revision reimport, defensive OOXML, CriticMarkup learning state, and optional Google suggestions/comments are U-09/D-06. |
| D9 Lifecycle and integrity | PARTIAL | Cache staleness, close inventory, sync guard, locks, backups, and basic attestation exist. Full commitment, fact versions/state digest, close-policy enforcement, learning-event state, and stronger signed heads are U-03/U-09/U-11A and D-09/D-14. |
| D10 Python foundations | PARTIAL | Domain models, strict schemas, migrations, five-layer frozen config, unions, protocols, sync-core boundaries, folds, and typed trust zones exist. Gate/CLI structure is U-05; learning package split is U-09. |
| D11 Agent-native parity | PARTIAL | Current run/decision/attestation/export and TaskSpec-lock primitives have CLI/API paths. Full learning actions, sidecars, `context.md`, emergent-task proof, and the complete capability matrix are U-09/U-11A/U-11B. |
| D12 Vocabulary, IDs, config | PARTIAL | Five-layer resolution and structural override controls are complete, as are the IDs consumed by immutable run context. Remaining IDs and gate execution order are U-05/U-09. |
| D13 Sequencing | PARTIAL / DEFERRED | The pre-serve core mostly exists but clean validation is U-17. Edit-learning, strategies, oracle, CLI breadth, and benchmark are U-06/U-09/U-10/U-11A/U-11B; Google/non-discovery breadth remains D-06/deferred. |

## Plan 2 — Demo Server and Deployment

Source: `docs/plans/2026-07-11-002-feat-demo-server-and-deployment-plan.md`
(`status: completed`).

| ID | Planned task | Status | Evidence or queue |
|---|---|---|---|
| DEMO-1 | Deterministic baked synthetic vault and read-only API | COMPLETE | Bake/API code and integration coverage exist. |
| DEMO-2 | Accessible courtroom-ledger viewer | COMPLETE | Static viewer is present and served by the demo app. |
| DEMO-3 | Docker build/deploy scaffolding and documentation | COMPLETE | Container/deployment assets exist; deployed history is documented. |
| DEMO-4 | Bake/API/404/traversal/read-only tests and README | COMPLETE | Current test suite covers these contracts. |
| DEMO-A | Green checks, attested/restructured bake, vault-only reads, secretless health, no writers | COMPLETE | Current local validation and invariants pass. |

The later `www` certificate/redeploy note is not part of this plan's acceptance
criteria. It remains an ask-gated production housekeeping choice in D-08.

## Plan 3 — Hosted Frontend FOLIO Cockpit

Source: `docs/plans/2026-07-12-001-feat-hosted-frontend-folio-cockpit-plan.md`
(`status: active`).

### Phase ledger

| ID | Planned task | Status | Evidence or queue |
|---|---|---|---|
| FE-0a | Threat model, registry, JWT/internal auth, rate limit, CSRF, audit, API invariants | COMPLETE | Code and regression tests exist. |
| FE-0b | Access/DNS/AOP/Coolify infrastructure and 13-point live penetration gate | COMPLETE, stale checkboxes | Deployment handoff records completion. No private matter content was needed to verify the recorded state. |
| FE-0c | Enforced outbound egress jail and per-matter OS isolation | OPEN-AUTO plus deployed proof | Provider wrapper is optional and the deployment shares one matters-root UID. U-02 implements the enforceable contract; D-03 authorizes deployed proof. |
| FE-1a | Provider, queue/worker, pause/resume/reopen, spend ledger, SSE, consistent backup, driver CLI | COMPLETE | Engine and recovery modules/tests exist; PRs #30/#31 hardened them. |
| FE-1b | Real-provider synthetic server gate | PARTIAL / DECISION-GATED | A hosted real-provider run occurred, but not a clean planted-injection gate under the final isolation contract. D-03. |
| FE-1c | Seat-limit push, one-tap bounded API failover, auto-resume | OPEN-AUTO, then live test | Pause/reschedule seam exists; notification/failover authorization flow does not. U-15. |
| FE-2 | Next.js cockpit, decision inbox, start/pause/continue/attest/export | COMPLETE | Frontend rooms, BFF/API clients, and tests exist. Historical phone flow reached the hosted run. |
| FE-2.5 | Thin freeform TaskSpec on-ramp | COMPLETE | TaskSpec service/API/UI exists; code explicitly reserves synthesis for FE-3. |
| FE-3 | Catalog/wizard/suggestion on-ramps, synthesis, derived-rubric review and hard lock | OPEN-AUTO | Only freeform is wired. U-12. |
| FE-4 | Durable StrategyBoard, jobs, React Flow UI, approve-then-inject, gaps/rubric overlay, reviewable auto-findings | OPEN-AUTO | No board implementation exists. U-13. |
| FE-5a | Hardened upload/tagging/needs-triage and suggestion events | OPEN-AUTO | U-14. |
| FE-5b | Drive OAuth watcher and cursor, deadline scheduler | DECISION-GATED then OPEN-AUTO | Requires product/consent decision D-06; queued in U-14 if approved. |
| FE-6 | Dashboard, audit room, per-passage/citation views, downloads, ntfy/digest/mute | OPEN-AUTO | U-15. |
| FE-7a | Security regressions, hosted seed, first hosted run, runbook/docs | PARTIAL | Security code and a first run exist; the run was blind to facts and occurred before final sandbox proof. |
| FE-7b | Clean full hosted pass, attorney decisions, attestation, live cutover | DECISION-GATED | D-01 through D-03. The July run is not accepted as clean validation. |
| FE-CI | Frontend lint/typecheck/tests, OpenAPI drift, and BFF-thin invariant in CI | COMPLETE locally | GitHub workflow now runs backend schema generation/diff, frontend lint/typecheck/tests/client drift/build, and lexical plus AST/mutation BFF-thin checks. Remote CI awaits commit/push. |

### Condensed acceptance criteria

| Acceptance criterion | Status |
|---|---|
| Direct-origin/cross-app rejection, valid Access JWT, access/download audit | COMPLETE based on recorded live penetration gate plus current code tests |
| Hosted start-to-finish phone run with decisions and attestation | DECISION-GATED; prior run did not finish cleanly |
| Seat-limit pause/push/auto-resume and bounded failover | OPEN-AUTO |
| Three on-ramps plus adapter/rubric synthesis and lock | OPEN-AUTO |
| Strategy board and reviewable system edits | OPEN-AUTO |
| Drive watcher, triage, suggestion, push | DECISION-GATED then OPEN-AUTO |
| Demo and vault/privacy boundaries intact | COMPLETE locally; deployed isolation upgrade remains U-02/D-03 |
| Backend and frontend checks in CI | COMPLETE in workflow and locally; remote run awaits commit/push |

### Exhaustive FD-1–FD-10 deepening disposition

| ID | Reconciled status | Evidence or queue |
|---|---|---|
| FD-1 Sandbox/internal trust | PARTIAL | Persona turns receive no filesystem tools; driver auth, queue, and SSE seams exist. Enforceable egress/per-matter isolation and planted-injection proof are U-02/U-17A. |
| FD-2 Perimeter | PARTIAL | JWT algorithm/audience/email/JWKS behavior and recorded AOP perimeter are complete. Device-only Google consent and connector/backup credential handling remain D-06/U-14. |
| FD-3 Non-portable controls | PARTIAL | Secret redaction and hash-chained access audit exist. Runtime outbound canary, stronger audit/attestation commitment, and content-free notifications are U-02/U-03/U-15. |
| FD-4 Approve then inject | PARTIAL | Approval-filtered, provenance-tagged, DATA-fenced manifest injection is complete; durable board/changelog/review feed is U-13. |
| FD-5 Architecture corrections | PARTIAL | Pause/queue/SSE/BFF, immutable launch bindings, and thin TaskSpec paths exist. Full task lanes, drain/failover behavior, and pipeline-shape deferral are U-11A/U-11B/U-12/U-15. |
| FD-6 Data lifecycle | PARTIAL | Consistent backup, spend intent, close inventory, and queue locking exist. Full integrity/recovery/close policy, board mutation, hardened upload, and watcher reconciliation are U-03/U-11A/U-13/U-14. |
| FD-7 Capability parity | PARTIAL | Current matter/run/decision/attestation/export rows exist. The checked capability matrix and remaining task/board/suggestion/failover/connector/notification verbs are U-11A/U-11B through U-15. |
| FD-8 TypeScript contract | COMPLETE for FE-2 | Generated OpenAPI types, drift checks, typed modules, zod SSE, session-expiry handling, query keys, protected mutation semantics, and thin-BFF tests exist. New surfaces must extend the same contract in U-12–U-15. |
| FD-9 Design direction | PARTIAL | The existing cockpit implements the case-file language and core ceremonies. The promised durable `docs/design/frontend-direction.md` plus remaining rooms/mobile board are U-11A/U-12–U-15. |
| FD-10 Sequencing | PARTIAL | FE-0 through FE-2.5 are substantially present, but the clean first run is not accepted; U-17 precedes the queued FE-3–FE-6 expansion under D-10. |

### Explicitly deferred frontend scope

`pipeline_shape` registry, Dropbox/OneDrive, Web Push, and multiplayer remain later
roadmap items recorded in the continuation plan's Deferred Follow-up Queue so they
cannot disappear. They are not promoted
ahead of context reproducibility, isolation, and a clean live run.

## Recent Non-Plan Obligations Inside the 21-Day Window

| ID | Source | Obligation | Status / routing |
|---|---|---|---|
| RECENT-1 | 2026-08-06 orientation handoff | Review and land PRs #30 and #31 | COMPLETE; both merged 2026-08-06. |
| RECENT-2 | PR #30 | Monitor the next three hosted runs or seven days for duplicate turns, spend/lock anomalies, and export-attestation integrity | NOT EVIDENCED; D-07. |
| RECENT-3 | PR #31 | Monitor the next three reopen operations or seven days | NOT EVIDENCED; D-07. |
| RECENT-4 | 2026-08-05 blind-turn audit | Audit/discard suspect hosted turns or re-drive after the fix | DECISION-GATED; D-01/D-03. |
| RECENT-5 | Historical live handoff | Resolve four attorney decisions and provide verified grounding facts | DECISION-GATED; D-02. No private answers belong in this repository. |
| RECENT-6 | PR #30 durable follow-ups | Fact-store torn-tail recovery; lost-lock/background heartbeat; backup heartbeat; shutdown and seat-limit attempt accounting | OPEN-AUTO; U-03/U-11A before U-17B. |
| RECENT-7 | PR #31 product follow-up | Expose `needs_attention` reopen reason/attempt/queue-repair state in the cockpit | OPEN-AUTO; U-11A/U-15. |

## Canonical Disposition

Every discovered task now has one of four durable outcomes:

1. `COMPLETE` with current evidence above.
2. `OPEN-AUTO` as U-02 through U-17C in the continuation plan; U-00, U-01, and
   U-01's U-18 publication disposition are complete. Deployment remains a separate
   gated operation rather than part of that publication closure.
3. `DECISION-GATED` as D-01 through D-17 in the Decision Sheet (D-15 through D-17
   have safe defaults and do not block pre-validation work).
4. `DEFERRED` but retained explicitly in the continuation plan rather than silently dropped.

On 2026-08-20 the user answered D-01 through D-17. The rulings are recorded in the
Decision Sheet. Some operations remain intentionally gated even after their policy
choice: D-03 A requires a fresh authorization before any real hosted-matter access;
D-06 B does not self-grant Google OAuth consent; D-08 A is read-only; D-09 A requires
approval of a named remote sink. D-13 A initially permitted only a PR/CI run; the user
later authorized merge of PR #32 and the reviewed continuation work. PR #33 merged as
`74dec0a`; deployment remains unauthorized.
The FOLIO source/update investigation required by D-05/D-12 is recorded in
`docs/research/2026-08-20-folio-integration-route.md`.

This audit is a status ledger, not permission to inspect privileged hosted artifacts,
record attorney approvals, consent to OAuth, spend uncapped funds, or deploy production.

## Autonomous Work Completed During This Audit

- Closed U-00: README truth, frontend CI, authoritative backend OpenAPI drift,
  regenerated contracts, production-build verification, and a mutation-tested AST BFF
  boundary. PR #32 merged after remote backend, invariant, and frontend CI passed on
  code head `d64960c` and one close-inventory repair.
- Completed U-01: exact byte-derived launch provenance;
  write-once manifest and separately hashed corpus snapshot; request/fact/policy/task/
  rubric/corpus replay; crash-recoverable launch; protected-decision and attestation
  checks; snapshot-backed gates, status views, panels, and court exports. Every
  lifecycle read now verifies the corpus digest, provider-return writes rebind context,
  corpus snapshots have launch/retained-size ceilings, and hosted starts use a stable
  run id plus a journaled, idempotently drained queue intent. Five-layer frozen config,
  in-memory migrations, canonical manifest IDs, exact human TaskSpec locks, and bounded
  permission-filtered context assembly close the formerly listed U-01 residuals.
- Preserved historical journals as readable status records with an explicit
  `replayable=false` blocker while continuing to reject resume, decisions,
  attestation, and export without a committed context.
- Applied structured-review fixes for live export drift, corpus drift, source-hash
  races, manifest-only launch recovery, idempotent decision/journal reconciliation,
  launch-chrome attestation, trusted CLI actors, provider-boundary writes, hosted
  start retries, non-replayable UI controls, BFF semantic drift, and unknown-run 404s.
- Resolved the PR review's P1 persona drift finding by snapshotting every authored
  persona body with provenance and rendering prompts/provider calls from launch context.

No hosted matter data, deployment, attorney approval, OAuth grant, or production state
was read or changed. Those items remain on the Decision Sheet.

## Final Local Verification — 2026-08-21

- `make check`: passed — ruff, strict mypy across 92 source files, and 848 pytest tests
  at 92% coverage (one existing Starlette deprecation warning).
- Frontend: ESLint, TypeScript, 9 Vitest files / 37 tests, backend and client OpenAPI
  drift checks, and the Next.js 16.2.10 production build passed. Next.js emitted its
  non-blocking middleware-to-proxy deprecation warning.
- `git diff --check`: passed.
- PR #32 and PR #33 are merged. PR #33's backend, invariant, and frontend jobs passed
  on head `acc1e51`; its one actionable review finding was fixed, replied to, and
  resolved before merge. U-18 is complete for U-01. Deployment, hosted-vault access,
  and the synthetic/real-matter gates remain unperformed.
