# Security review reconciliation — 2026-09-30

Scope: local repository evidence only, on `chore/u12-exec-20260930`, initially
`befda494ba3a6f81a7556f617824a26af60975bb`. No hosted artifacts, credentials,
protected matter data, provider calls, or production endpoints were inspected.

## Completed engineering work

The on-deck card's “IN FLIGHT” label is stale for the merged security fixes.
`git show -s --format='%H%n%P%n%s' 341125c` resolves to:

- merge: `341125c6c15e7b15cd74c5f9a6282886f095dd1c`, PR #30;
- first parent: `90c68f7e06074cc94b3e8c2b648276cb144fd936`;
- second parent: `989bce0310e0d02b95abec22bd1199d2814b4999`.

`git merge-base --is-ancestor <commit> HEAD` returned 0 for that merge and every
reviewer-cited security commit: `467e812`, `0291062`, `a9fad0c`, `e5fb83a`,
`cffcae9`, `dbbad4a`, `62769d8`, `8562ccd`, `c25627f`, `9674938`, `8e6bfdc`,
`b5a7639`, `7be1280`. This proves inclusion in this branch, not current hosted
health. The provider implementation and coverage are in
`src/mootloop/engine/claude_provider.py` and `tests/unit/test_engine_provider.py`.

## Residual scope stays open

`docs/audits/2026-08-05-blind-persona-turn-audit.md`, sections 5–6, explicitly
cannot determine hosted historical-run trust from the available local artifacts.
The September 30 direction keeps real-matter use **NOT YET**. A fresh protected
read or replacement run needs named scope and authorization; this worker did neither.

`src/mootloop/gate_ledger.py::_turn_gate_status` permits an operative draft's cured
finding to supersede an earlier failed finding while retaining historical worst
statuses in `GateLedgerDoc.superseded`. The API reader in
`src/mootloop/web/api/readers.py::gate_ledger_response` returns operative gates and
raw journal turn gates, but not that derived `superseded` map. Therefore history is
not wholly hidden, and API presentation is distinct from the unresolved substantive
policy of explicit attorney acknowledgment before relying on corrected fabrication.
No new acknowledgment policy is implemented or deemed approved here.

## Prepared queue replacement and historical receipt

The orchestrator can replace the completed engineering portion with:

> Resolve the security review's remaining historical-run trust and cured-fabrication
> acknowledgment policy. PR #30's engineering changes are already merged. Keep
> affected historical outputs untrusted pending explicitly authorized review or a
> replacement run; obtain the attorney's policy decision before changing cure semantics.

Preserve lineage to the original card and
`mootloop-2026-08-05-1020-engine-security-review/q1-persona-sandbox-blind`.
Do not reopen the answered August questions or retire this composite card as done.
The orchestrator may file a historical merge receipt after binding it to a real
sweep verdict ID; this document is evidence preparation, not a filed Cockpit receipt.

Historical rollback pointer: `git revert -m 1 341125c6c15e7b15cd74c5f9a6282886f095dd1c`.
This was not executed and is not recommended: it would reopen security defects.
Rollback of this documentation is a focused revert of its own commit.

This preparation belongs on default `main` independently of homepage work.
Verification results are recorded in `agents/run-notes/u12-exec-mootloop-20260930.md`.
