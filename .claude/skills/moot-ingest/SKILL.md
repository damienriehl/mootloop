---
name: ingest
description: Ingest attorney-selected documents into a private MootLoop vault and report triage without making privilege decisions.
disable-model-invocation: true
argument-hint: <vault-path> <source-dir> [--tags <yaml>]
---

# MootLoop ingest

This mutates a private vault and must be invoked explicitly by the user.

1. Run `uv run mootloop validate <vault-path>`.
2. Run `uv run mootloop ingest $ARGUMENTS`.
3. Run `uv run mootloop corpus actions <vault-path>` and report conversion or triage items.
4. Do not infer privilege or production status. Those are human decisions recorded through
   the corresponding primitives.
