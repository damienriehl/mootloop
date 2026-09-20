# Adversarial review remediation — 2026-09-19

The user lifted the review-only restriction and authorized fixes after local review
commit `0a508aa`. The findings in
`docs/reviews/2026-09-19-adversarial-review.md` remain the historical source evidence.
This record tracks repairs and observed verification separately.

Scope remains local and synthetic only. No real vaults or credentials, network,
push, PR, merge, deployment, or migration is authorized. D-10 remains pending;
the U-12/U-13 scaffolding branches are not merged or advanced by this repair work.
Pre-existing `.claude/RESUME.md` and the untracked pickup handoff are excluded.

## R02 / R14 / R15 — journal and decision-log commit boundaries

Readers now ignore only newline-less terminal fragments without changing the
underlying file. Invalid complete records raise validation errors even at EOF.
Writers use the existing `append_fsync_line` primitive, which holds an exclusive
file lock across incomplete-tail repair, append, and file fsync; it also fsyncs
the parent directory. Decision reads use the existing complete-line iterator.

Before production changes, strengthened/new journal tests observed **5 failures
and 21 passes**. They reproduced reader mutation, deletion of a concurrent
recovery append, acceptance of an uncommitted complete JSON object, suppression of
complete-record corruption, and concatenation after an unterminated valid object.
The decision regression observed **1 expected failure and 1 pass**: an interrupted
append prevented exact resolution retry, while complete corruption already failed.

After repair, both focused suites passed: **39 tests in 1.56 seconds**.
Independent source review reported no actionable findings in this scoped repair.
The first full `make check` passed lint, strict mypy, and **1,244 tests** in
379.52 seconds (two DOCX tests skipped, one paid test deselected). After making
the installed Pandoc visible inside the isolation wrapper, both skipped DOCX
tests passed in 4.63 seconds. Final combined validation is recorded below.

Verification runs through a disposable bubblewrap environment with the real user
home hidden, only the repository/interpreter/uv/Pandoc/Node rebound, environment cleared,
network disabled, and synthetic test state under its private `/tmp`. No paid tests
are enabled. The outer sandbox needs an approved escalation solely to create that
namespace. Test harness and raw logs under `/tmp` are temporary; this audit retains
the substantive evidence.

## Additional repairs

| Findings | Implemented repair | Regression evidence |
| --- | --- | --- |
| R01 | Decisions and spend precede completion; completion without usage retains its reservation; completed-turn retry and idle worker recovery reconstruct final gates and finalization. Citation checks settle spend before completion. | Completion/reservation/worker regressions initially produced eight failures; corrected focused completion and learning suites passed 26 tests. |
| R03, R04 | Restore preflights location and serializes publication with create/close; default promotion never deletes a competing target. | Privacy/output regressions initially produced 29 failures and two passes; subsequent broad run passed 263 tests with one unrelated output-fixture failure. |
| R05 | Persist an off-vault CloseIntent, detach the prepared vault into private quarantine, then purge; retries operate only on that original. An undetached retry rechecks retention, backup, and inventory. Deduplicate tombstones and preserve replacement vaults. | Both destructive-boundary tests failed before repair; recovery suite includes partial purge, failed intent fsync/retry, changed litigation hold, replacement identity, and close inventory. Final close/output/inventory suites: 52 passed in 0.81 seconds. |
| R06 | Serialize first-use key creation and atomically persist one winner; concurrent reads wait for publication durability. | First-use races reproduced; additional durability test failed before shared-lock fix. Final recovery/privacy/key suites: 66 passed in 1.47 seconds. |
| R07, R08, R19 | Strict registry shape validation; locked atomic canary registration before vault publication; restored canaries registered or legacy archives seeded. | Malformed policy, lost concurrent registration, failed creation, and restore registration regressions included in the privacy suite. |
| R09 | Reject duplicate request IDs, duplicate served-set labels, and inconsistent set numbers before launch and when materializing historical snapshots. | New identity regressions reproduce ambiguous joins; disjoint numbering remains supported. |
| R10, R11, R12 | Preserve reviewed RFA narrative/disposition and RFP narrative; incompatible choices block clean export and explain required revision in draft copies; existing seals cannot bypass current gates. | Final output and DOCX integration suites: 35 passed in 115.84 seconds. |
| R13 | Exact acceptance retry reuses the durable human review and republishes a missing immutable context contribution; conflicting choices remain rejected. | Interrupted publication failed before repair; final acceptance/review tests passed. |
| R16 | A replayed run_started rebuilds stream state from the journal beginning. | Replay test failed before repair, then passed; frontend suite: 44 tests across 13 files, 2.12 seconds. |
| R17 | Validate worker identifiers before heartbeat path construction, preserving existing safe uppercase identifiers. | Five unsafe-identity cases failed before repair and pass after it. |
| R18 | Require directory/config identity agreement on restore, listing, and normal resolution; recovery enumeration retains structural-only resolution. | Archive and registry mismatch regressions pass. |

The synthetic demo registry moved beside its vault: staged vault creation cannot
publish over a destination prematurely created by an internal registry write.
The complete backend suite exposed this fixture incompatibility; no live data
was read or migrated.

## Review and final validation

The simplification dispatch hit the agent-thread limit. The three supplied reuse,
quality, and efficiency rubrics were applied inline; no additional simplification
was retained because the candidate changes would alter safety checks or ordering.
The CE code review completed against base `0a508aa` and this repair tree:
`status: complete`, verdict **Ready with fixes**, run
`20260919-remediation-sgyfeszv`. Ten reviewer receipts covered correctness,
standards, testing, maintainability, security, reliability, adversarial failure
paths, performance, frontend races, and an independent quarantine follow-up.
Source inspection marked all 19 repair targets met. One P2 performance finding
remains deferred below; no current P0/P1 findings remain. Reviewers did not run
tests; the root agent owns verification. External peer review was disabled by the
user's network prohibition. The raw receipt is temporary under `/tmp`; this
committed audit preserves its result and remaining concern.

Final combined **`make check` passed**: Ruff, strict mypy across 135 source files,
and **1,311 tests in 435.64 seconds**, with one paid test deselected and one
warning. No tests skipped. Coverage was 91%. Frontend ESLint and TypeScript checks
passed, along with **44 frontend tests across 13 files**. The focused local commit
contains only this remediation and its evidence; unrelated resume/handoff files
remain excluded. Nothing was pushed, merged, or deployed.

Review-driven regressions reproduced and repaired: a visible but nondurable close
intent on retry; identical legacy usage after a discarded call suppressing a new
charge; reuse after failed key fsync; stale retention/backup evidence after failed
close preparation; and unconditional acceptance of a privilege-withhold choice.
The first three repair suites passed 49 tests in 1.44 seconds. The final two
regressions initially produced two failures and ten passes before their repairs.
Recovery reconstruction now runs at finalization, after the all-requests-complete
check, rather than rescanning completed drafts after each ordinary turn. No
concurrency setting or caching code was changed.

Final recovery derives missing gates in a batch using one decision-log fold.
Decision/completion/spend/planning suites passed 36 tests in 2.31 seconds. The
additional rename-before-detached-intent-commit regression and decision suite
passed 21 tests in 1.40 seconds.

### Deferred review finding

- **P2 — additional ledger fold**, CE performance reviewer, independently
  source-validated: `src/mootloop/gate_ledger.py:139` calls `operative_drafts`,
  adding a journal fold and request traversal to the two existing folds reached
  through `_turn_gate_status` and `operative_draft_turn_ids`. Large journals pay
  this extra linear work on ledger/UI/export reads, including when there are no
  decisions. Perceptible latency and operational incidence were **not measured**;
  no incorrect result was identified. Suggested follow-up: compute operative turn
  IDs and drafts from a single already-loaded state/context. Deferred because it
  is a broader performance refactor beyond the 19 correctness/security repairs;
  U-16 remains measurement-only, with concurrency and caching decisions gated.
  This concern remains visible for branch review and is not represented as fixed.

## Practical limits

Follow-up on the deferred P2: the duplicate ledger traversal and journal folds
were subsequently removed and measured in
[`2026-09-20-gate-ledger-remediation.md`](2026-09-20-gate-ledger-remediation.md).
The review and verification results above remain the historical repair record.

- Overlapping opponent request numbers remain unsupported in a single run and now
  fail explicitly; this change does not migrate request identities.
- A choice requiring new legal language needs a revised response and attorney
  review in a new run. This repair does not invent an in-place revision workflow.
- Privilege choices cannot currently establish withholding/production/logging in
  the draft schema, so all resolved privilege calls block clean export. Draft
  review copies remain available. A supported reviewed disposition is required
  before clean privilege exports can be enabled; a new run alone does not fix this.
- Legacy missing usage cannot be reconstructed from absent data. Outstanding
  reservations remain conservative until a spend event settles them.
- Failed unpublished creation may retain an extra registry binding, which adds a
  privacy tripwire. Explicit destructive restore with `overwrite=True` retains its
  existing rollback limitations.
- Local commits do not deploy these fixes or authorize D-10, concurrency, or caching changes.
