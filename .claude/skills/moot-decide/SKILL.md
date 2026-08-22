---
name: decide
description: Present MootLoop attorney-gate decisions and record only the human's explicit selected resolution.
disable-model-invocation: true
argument-hint: <vault-path> <run-id>
---

# MootLoop decide

Decision resolution is a hard-human act. Never choose, approve, deny, or modify on the
attorney's behalf.

1. Run `uv run mootloop decide list <vault-path> <run-id>`.
2. For a selected item, run `uv run mootloop decide show <vault-path> <run-id> <decision-id>`.
3. Explain the options and recommendation without treating it as authority.
4. Only after the attorney states the choice, record it with `uv run mootloop decide resolve`
   using the exact requested action and option. The CLI derives the trusted local actor.
5. Re-read the decision and run status; report the durable result.
