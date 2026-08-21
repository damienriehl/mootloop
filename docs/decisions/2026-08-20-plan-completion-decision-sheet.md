# Decision Sheet — Completing MootLoop's Open Plans

## How to use this sheet

Reply with only the decision IDs and option letters, for example:

```text
D-01 A
D-02 A
D-03 A
```

You can answer one at a time. Option **A** is the recommended default in every row.
No client facts, credentials, privileged excerpts, or substantive legal decisions
should be written into this repository; enter those only through the protected cockpit
or another attorney-approved channel.

## Recorded rulings — 2026-08-20

These rulings are settled implementation constraints for the continuation queue:

| ID | Ruling | Operative effect |
|---|---|---|
| D-01 | A | Quarantine suspect hosted turns; do not reuse them. |
| D-02 | B, plus a live-protected-data-capable build | Use synthetic inputs for current validation. Build the same path to enforce protected-data boundaries, but D-03 A still requires a fresh authorization before reading or running a real hosted matter. |
| D-03 | A | Synthetic deployed gate first; ask again before any real-matter read or run. |
| D-04 | A | Prove synthetic conversion/error handling, then review the protected manifest interactively. |
| D-05 | B, with comparison | Integrate `folio-enrich` directly and evaluate the FOLIO rendering module as a complementary component. |
| D-06 | B | Build local DOCX learning and the Google Drive/Docs lane in parallel; OAuth consent remains human-only. |
| D-07 | A | Record redacted monitoring during the next authorized operations. |
| D-08 | A | Verify production housekeeping read-only; propose mutations separately. |
| D-09 | A | Use OS-keychain signing plus an approved immutable remote signed-head sink; provider details require a separate concrete approval. |
| D-10 | A | Gate backlog expansion on the smallest clean workflow and observed attorney friction. |
| D-11 | A | Authenticated Confirm is the human lock; append actor/time/task/rubric digests. |
| D-12 | C, with canonical-source direction | Use `alea-institute/FOLIO` `main/FOLIO.owl` as source of truth and implement the proven startup/periodic freshness pattern. Active runs remain pinned. |
| D-13 | A, merge later authorized | Create focused commits and a PR, repair remote CI, and merge after green review; deploy remains separately gated. |
| D-14 | A | Persist and validate `source_matter_id` on every matter-scoped model. |
| D-15 | A | Context changes always require a new run. |
| D-16 | A | Order expansion from friction observed in the clean compounding loop. |
| D-17 | A | Keep the named collaboration/integration breadth deferred. |

For D-05/D-12, the implementation route and source inspection are recorded in
`docs/research/2026-08-20-folio-integration-route.md`.

## Original questions and options

### D-01 — What should happen to the suspect prior hosted turns?

**Why this matters:** the August audit found that personas were not given the intended
matter context. Treating those turns as valid could contaminate later work.

- **A — Discard/quarantine them and re-drive after the new context/isolation gates
  (recommended).** Fastest safe path; preserves only non-substantive audit metadata.
- B — Perform a privileged forensic content review first, then decide what to retain.
  More evidence, but requires explicit hosted-vault access and attorney review.
- C — Preserve them as historical artifacts but prohibit all reuse in prompts,
  exports, learnings, and benchmarks.

**What your choice unlocks:** the clean-live-validation protocol in D-03.

### D-02 — Are you ready to provide the attorney inputs for a clean live run?

**Why this matters:** the previous hosted run stopped with four unresolved attorney
gates and zero verified client facts loaded into the prompt context.

- **A — Yes, after the cockpit presents a compact checklist (recommended).** The
  system prepares the questions; you answer inside the protected cockpit.
- B — Use a synthetic matter only for now; defer all real-matter judgment.
- C — Pause live validation entirely while autonomous core work continues.

**You will be asked for:** decisions on the outstanding gates, a minimum verified fact
set with provenance, and permission to attest only after reviewing the final output.
The agent must never invent or self-record any of these.

### D-03 — How much hosted/deployment access is authorized for validation?

**Why this matters:** local tests cannot prove the deployed egress jail, per-matter OS
isolation, real-provider behavior, Access perimeter, or operational monitoring.

- **A — Synthetic deployed gate first; ask again before any real-matter read or run
  (recommended).** Authorizes only synthetic content and redacted operational metadata.
- B — Synthetic gate plus a clean real-matter re-drive after D-02 is complete.
- C — Repository-only work; do not access or change hosted systems.

**Never implied by this choice:** production deploys, uncapped spend, substantive
attorney approval, or broad hosted-vault browsing.

### D-04 — What is the target for the remaining real-folder ingest failures?

**Why this matters:** historical ingest surfaced many documents needing conversion and
one oversized item. Generic code is present, but “the real case folder ingests clean”
cannot be certified without private inputs.

- **A — Build and prove a synthetic conversion/error workflow first, then review the
  protected real manifest interactively (recommended).**
- B — Accept surfaced conversion items as an honest manual work queue for v1.
- C — Require automated normalization of every supported document before live work.

### D-05 — Keep, replace, or retire the planned `folio-enrich` ingest dependency?

**Why this matters:** the plan names a localhost-only integration, but current MootLoop
does not call it. Building around an obsolete dependency would waste a continuation
unit.

- **A — Keep the interface boundary but implement a provider-neutral local converter
  adapter (recommended).** `folio-enrich` can be one implementation later.
- B — Integrate `folio-enrich` directly now, localhost-only and fail-closed.
- C — Retire it and use MootLoop's internal normalization path only.

### D-06 — When should Google Drive/Docs integration enter the critical path?

**Why this matters:** Drive OAuth consent and GDoc round-trip add external permissions
and security surface. The local DOCX learning loop is not yet proven.

- **A — Prove local DOCX edit-learning first; revisit Google afterward
  (recommended).**
- B — Build GDoc export/comment reimport and the Drive watcher in parallel now.
- C — Drop Google integration from v1 and retain DOCX/manual upload only.

### D-07 — How should PR #30/#31 post-merge monitoring be closed?

**Why this matters:** both PRs requested monitoring of the next three relevant
operations or seven days, but no durable result is recorded.

- **A — Run a redacted metadata-only monitor during the next synthetic/authorized
  operations and record the result (recommended).**
- B — Close the obligation based on elapsed time and current regression coverage.
- C — Perform a privileged audit of the historical hosted operations.

### D-08 — Should old production housekeeping be included now?

**Why this matters:** a historical handoff mentions a `www` certificate/redeploy and
credential rotation. These are outside the completed demo plan's acceptance criteria
and may already be obsolete.

- **A — Verify current state read-only, then propose any production action separately
  (recommended).**
- B — Defer all demo-production housekeeping.
- C — Authorize a dedicated production maintenance session after a fresh runbook.

### D-09 — What off-vault integrity anchor should protect attestations?

**Why this matters:** a local hash chain detects accidental edits but a writer who
controls the vault can rewrite both content and hashes. A stronger tamper-evidence
claim needs a signing key and signed heads outside the writable matter vault.

- **A — OS-keychain signing key plus a minimal immutable remote signed-head ledger
  (recommended).** The ledger contains digests and timestamps only, never matter text.
  This selects the architecture, not a provider: before implementation, the agent must
  present a named sink, API/auth path, retention/immutability guarantee, fail-closed
  behavior, and recovery procedure for a separate approval. No remote write is implied.
- B — OS-keychain signing only; retain signed heads locally and in encrypted backups.
- C — Keep local hash chains for v1 and describe them only as corruption detection,
  not host-compromise evidence.

### D-10 — Should clean workflow evidence gate backlog expansion?

**Why this matters:** persona modes, optional panels, RFP assistance, FE-3 through
FE-6, and performance work add substantial maintenance surface. The prior run's actual
blockers were missing context, facts, and human decisions.

- **A — Validate the smallest complete workflow first, then expand from observed
  attorney friction (recommended).** Safety, facts, integrity, local learning, and a
  clean run stay first; every other item remains durably queued.
- B — Continue the entire autonomous backlog in dependency order while live validation
  waits for attorney inputs.
- C — Prioritize the hosted cockpit phases before the remaining v1 core.

### D-11 — What should count as the human TaskSpec lock?

**Why this matters:** the begin-task screen already asks the attorney to review a
resolved slip and press Confirm, but the append-only TaskSpec record does not capture
that approval actor or the exact task/rubric digest. The future suggestion and
synthesis lanes must not launch from an agent-generated contract without a human lock.

- **A — Treat authenticated Confirm as the lock and append actor/time/digest
  provenance (recommended).** One clear review step; start rejects unlocked or stale
  derived specs.
- B — Add a separate Lock Contract step before Confirm Run. More explicit, but adds a
  second attorney action to every launch.
- C — Keep deterministic freeform specs runnable without a lock; require locking only
  for future wizard/suggestion/synthesis lanes.

**What your choice unlocks:** the remaining U-01 TaskSpec contract and its CLI/API/UI
parity tests. Until chosen, existing direct task starts remain available, but the
planned derived-contract guarantee is not marked complete.

### D-12 — How should the pinned FOLIO catalog dependency be acquired?

**Why this matters:** FE-3 names FOLIO as a local, pinned task catalog, but the
repository contains no approved release, license record, or content manifest. A live
catalog fetch would make synthesis non-reproducible.

- **A — Keep FE-3 blocked until the agent presents one official release, its license,
  content hash, and local manifest for approval (recommended).** No runtime network
  dependency and no unreviewed third-party asset enters the repository.
- B — Drop FOLIO from this milestone and ship only freeform/local-adapter on-ramps.
- C — Authorize a separate catalog-source investigation now, but do not implement or
  import anything until you approve the resulting source sheet.

**What your choice unlocks:** U-12 source selection. Option A still requires one
short, concrete release approval after the evidence packet exists.

### D-13 — May the reviewed local work be published for remote CI?

**Why this matters:** U-00/U-01 are locally green but remain uncommitted. GitHub CI
cannot validate them, and the timer-managed resume file plus unrelated dirty state
must not be swept into a commit.

- **A — Create focused commits on a clean branch, push a PR, and observe/repair CI;
  ask again before merge or deploy (recommended).**
- B — Create focused local commits only; do not push.
- C — Keep all current work uncommitted.

**Never implied by this choice:** merge, production deploy, hosted-vault access, or
permission to include unrelated dirty files.

**Execution status (updated 2026-08-21):** Option A was carried out through focused
commits, PR #32, remote CI repair, and PR-feedback repair. The user subsequently
authorized merge of PR #32 and the reviewed continuation work. PR #33 merged as
`74dec0a` after all remote checks passed and its actionable review thread was resolved;
every deployment remains withheld for a fresh inline authorization.

### D-14 — How literal should `source_matter_id` persistence be?

**Why this matters:** the original plan says every matter-scoped model carries a
source matter ID. Several current records are instead bound by their safe path inside
one matter vault. Both can prevent cross-matter reads, but only the first makes a
detached record self-identifying.

- **A — Add `source_matter_id` to every new or migrated matter-scoped persisted model
  and validate it against the containing vault (recommended).** Strongest detached
  artifact and migration semantics, with more schema work.
- B — Treat validated matter-vault containment as equivalent for records that can
  never leave that vault; require the field only on exports/cross-vault artifacts.
- C — Apply the explicit field only to new models; leave existing models path-bound
  until they otherwise migrate.

**What your choice unlocks:** U-11A close-inventory and source-binding acceptance.

## Decisions that can wait until the safety gates are green

### D-15 — May an active run ever rebase onto changed context?

- **A — No; every context change creates a new run (recommended).** Clearest audit and
  attestation semantics.
- B — Allow an attorney-authorized, journaled rebase that invalidates downstream
  turns, verdicts, and attestation before recomputation.

### D-16 — Which breadth should follow the first clean compounding loop?

- **A — Finish the compounding core and one clean live run, then prioritize from
  observed attorney friction (recommended).**
- B — Build calibrated-judge and non-discovery adapters before FE-3 through FE-6.
- C — Build FE-3 through FE-6 before calibrated-judge and adapter breadth.

### D-17 — Should currently deferred collaboration/integration scope enter the next milestone?

- **A — Keep Dropbox/OneDrive, Web Push, broad `pipeline_shape`, and multiplayer
  deferred (recommended).**
- B — Create a successor plan for selected items after U-17C; name them in notes.
- C — Create that successor scope now, without moving it ahead of the safety queue.

### D-18 — May hosted workers reach the three fixed public legal-source hosts? — UNANSWERED

**Why this matters:** U-07's application layer accepts only fixed paths on
`www.courtlistener.com`, `api.courtlistener.com`, and `www.revisor.mn.gov`; no ingested
text can choose a destination. The deployed authenticated proxy still permits only the
model endpoint, so hosted citation checks and judge-profile builds will fail closed
until this separate network policy is expanded.

- **A — Authorize exactly those three public legal-source hosts in the hosted CONNECT
  allowlist (recommended).** Keep the application fixed-path checks, proxy authentication,
  outbound privacy gate, and all other destinations denied.
- B — Keep the hosted proxy model-only. Citation checks and judge profiles remain
  local-only or explicit human research tasks.

**Never implied by this choice:** deployment, a live hosted run, protected-data access,
or permission to add any content-derived or broader internet destination.

## Copy/paste response

```text
D-01 A
D-02 A
D-03 A
D-04 A
D-05 A
D-06 A
D-07 A
D-08 A
D-09 A
D-10 A
D-11 A
D-12 A
D-13 A
D-14 A
D-15 A
D-16 A
D-17 A

Optional notes:
```
