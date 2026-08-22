---
name: setup
description: Create and validate a new private MootLoop matter vault from attorney-supplied configuration.
disable-model-invocation: true
argument-hint: <vault-path> --matter-id <id> [--from-yaml <path>]
---

# MootLoop setup

This mutates a private vault and must be invoked explicitly by the user.

1. Confirm the destination is outside the repository and outside background-sync storage.
2. Run `uv run mootloop init $ARGUMENTS`.
3. Run `uv run mootloop validate <vault-path> --json`.
4. Report the created path and validation result. Never copy secrets or matter data into
   the repository.
