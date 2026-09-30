# U12 mootloop execution — 2026-09-30

Worker scope: `chore/u12-exec-20260930` in the assigned worktree only. Local
preparation; no push, branch change, deployment, production call, send, spend,
credential access, or protected-matter run. The reviewer proposal was read fully.
September 30 answers supersede its older unanswered classifications.

## Per-item disposition

| Title | Outcome | Commits | Verification (command + result) | Exact next step |
|---|---|---|---|---|
| Security review | NEEDS-DAMIEN | `8e9f341` | Ancestry checks: 14/14; `make check`: passed | File prepared historical receipt; attorney settles acknowledgment policy and authorizes any protected audit/rerun. |
| Two-account AWS integrity ledger | DONE-LOCAL (plan only) | `fb53b99` | Five review lenses complete; `make check`: passed | Implement U1–U6; verify AWS semantics/pricing; Damien provisions accounts/custody and approves spend ceiling before remote acceptance. |
| Namecheap key rotation | SKIPPED (Damien hands-on) | `105c3f1` | Handoff/source checks; `make check`: passed | Damien rotates; operator verifies replacement success and old-key rejection from allowlisted Hetzner IP. |
| Off-box backup destination | NEEDS-DAMIEN | `558bf3d` | Retention/source checks; `make check`: passed | Confirm records schedule, then select FD6-01P A or exact B parameters. |

All four changes belong on default `main` independently of homepage work. Local Git
corrects the supplied branch-base note: starting HEAD was `befda494ba3a6f81a7556f617824a26af60975bb`,
the PR #71 public-homepage merge. `git merge-base --is-ancestor 738d7f2 HEAD`
returned 0. No rebase, fetch, or remote verification was performed.

## Artifacts and boundaries

- `docs/audits/2026-09-30-u12-security-reconciliation.md`: completed engineering
  evidence, residual scope, proposed queue wording and historical rollback pointer.
- `docs/plans/2026-09-30-feat-d09-two-account-integrity-ledger-plan.md`: implementation
  units, synthetic tests, cost assumptions, operator prerequisites, immutable rollback.
- `docs/decisions/2026-08-23-d09-remote-signed-head-sink.md`: dated approval addendum;
  old request retained as history.
- `docs/audits/2026-09-30-u12-registrar-operator-handoff.md`: skipped operator action.
- `docs/audits/2026-09-30-u12-backup-retention-handoff.md`: held decision and minimal
  next input; D-09 approval does not authorize backup retention.

The ledger plan's estimate is hypothetical, not a current quote or spending
approval. It includes daily full-chain sweep costs; those cannot be silently removed
from the approved protocol to reduce cost. No AWS accounts or credentials were
probed. Real-matter use stays **NOT YET**.

## Verification and execution notes

The worktree had no usable virtual environment. A frozen offline sync with a fresh
`/tmp` cache could not find mypy 2.2.0 and installed no usable test environment.
The successful testing route uses the existing checkout's installed dependencies
read-only, without synchronizing them; `PYTHONPATH` explicitly selects this worktree's
source, and bytecode generation is disabled. No main-checkout source was changed.
This is not proof of a fresh frozen-lock install.

The initial sandboxed `make check` passed lint and mypy but hung in the in-process
Starlette TestClient. A bounded diagnostic reproduced the wait and exited 124; the
same isolated synthetic API test passed outside the sandbox. The full gate was
therefore rerun with narrowly scoped escalation. Paid-oracle tests remain excluded.

A four-minute bound on the first escalated full gate expired while synthetic
pipeline cases were passing; it did not complete the gate. The final run removes
that insufficient time limit. The final full gate exited 0, taking 932.12 seconds.
The same green gate covers these documentation-only commits: no source, test,
dependency, or runtime configuration was changed after it. `git diff --check` also
passed. Existing-path references in all three operator/audit handoffs resolved.

Exact final command (run from this worktree, with scoped escalation for the reproduced
sandbox TestClient issue):

```bash
PYTHONPATH="$PWD/src" PYTHONDONTWRITEBYTECODE=1 \
UV_PROJECT_ENVIRONMENT='/home/damienriehl/Coding Projects/mootloop/.venv' \
UV_NO_SYNC=1 UV_OFFLINE=1 UV_NO_CACHE=1 make check
```

Output excerpts:

```text
uv run ruff check src/ tests/ tools/ --fix
All checks passed!
uv run mypy
Success: no issues found in 146 source files
uv run pytest tests/ -v --cov=mootloop -m "not paid_oracle"
collecting ... collected 1442 items / 1 deselected / 1441 selected
TOTAL                                              15606   1482    91%
===== 1435 passed, 6 skipped, 1 deselected, 1 warning in 932.12s (0:15:32) =====
```

The warning is the installed Starlette TestClient/httpx deprecation. It is not a
failure and no dependency changes were made. Six optional counterfactual tests were
skipped; paid-oracle coverage was deliberately deselected. This gate proves current
local regression health, not AWS retention enforcement or production readiness.

Noninteractive CE document review completed coherence, feasibility, scope, security,
and adversarial passes. One overly narrow protocol-field allowlist was corrected to
record-specific D-09 fields and independently verified; no material findings remain.
Cross-model review was skipped under the network-free restriction.

Security queue preparation preserves the original lineage
`mootloop-2026-08-05-1020-engine-security-review/q1-persona-sandbox-blind`. The
orchestrator must bind the historical receipt to a real verdict ID before filing;
this worker did not mutate Cockpit state. Raw turn gates remain available in the API,
while the derived superseded map is not exposed there. No new policy was implemented.

The ledger artifact is a reviewed implementation plan, not a built ledger. No source
module, transport, account, credential or remote anchor was added. Its plan-only
completion follows the card's explicit “Agent plans under CE” scope. The next local
implementation and later operator/remote acceptance remain outstanding.

## Delivery and status

Startup heartbeat initially failed because `python` was absent, then because the
status directory was read-only. Using `python3` with narrowly scoped escalation
succeeded; later atomic updates preserved the existing JSON. No heartbeat error was
silently ignored. Run-directory checks found no stop request at inspected boundaries.
Git staging likewise required escalation solely for this worktree's shared Git index.

The pre-existing `agents/tasks/u12-exec-20260930.md` is preserved and excluded from
all commits. Final status will be `review_ready`. No commits are pushed or merged by
this worker. The final report commit is deliberately last.
