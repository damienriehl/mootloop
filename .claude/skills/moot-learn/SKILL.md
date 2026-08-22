---
name: learn
description: Import edited MootLoop work, surface bounded learning proposals, and record only explicit human accept, reject, scrub, or promote choices.
disable-model-invocation: true
argument-hint: <vault-path> <run-id> <edited-docx>
---

# MootLoop learn

Learning import and governance mutate the vault and must be explicitly invoked. Acceptance
and promotion are human acts; an agent may prepare and explain proposals only.

1. Import with
   `uv run mootloop learn import <vault-path> <edited-docx> --run <run-id>`.
2. List and show proposals with `learn list` and `learn show`.
3. Present the anchored before/after change and proposed tier.
4. Record only the attorney's explicit `accept` or `reject` choice.
5. Before any firm promotion, run `learn scrub`, show the rendered diff, and require the
   attorney's explicit confirmation. Never publish to the OSS playbook from a matter vault.
