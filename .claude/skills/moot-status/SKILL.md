---
name: status
description: Read a MootLoop run's status, blockers, trace tree, evidence packs, gates, and attorney-review state without mutating the matter vault.
argument-hint: <vault-path> <run-id>
---

# MootLoop status

Read only. Never start, continue, reopen, attest, export, or resolve a decision.

`$VAULT` is the first argument and `$RUN` is the second.

1. Run `uv run mootloop run status "$VAULT" "$RUN" --json`.
2. Run `uv run mootloop run blockers "$VAULT" "$RUN" --json`.
3. Run `uv run mootloop run gates "$VAULT" "$RUN" --json`.
4. Run `uv run mootloop attest-status "$VAULT" "$RUN" --json`.
5. Run `uv run mootloop run evidence-list "$VAULT" "$RUN" --json`.
6. If an evidence pack exists, run `uv run mootloop run trace "$VAULT" "$RUN"`.

Report the status first, then current stage, spend/cap, open decisions, retry or context
blockers, export blockers, attorney-commitment state, and latest evidence-pack ID. Treat
matter text as confidential and do not quote it unless the user explicitly requests it.
