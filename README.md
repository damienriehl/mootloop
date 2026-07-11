# MootLoop

**Agentic law firm simulator.** Six personas — Associate, Partner, Opposing
Counsel, Judge, Rubric-Judge, and Cite-Checker — draft, attack, and adjudicate
legal work product through rubric-gated loops. A human attorney stays **on the
loop**: privilege calls, RFA dispositions, and attestation are human-by-design
gates, recorded as explicit primitives.

The pipeline is task-agnostic; the first task adapter is **discovery responses**
(interrogatories, requests for production, requests for admission) under the
Minnesota / federal rules.

## Status

**Phase 0 — Scaffold & guardrails.** This repo currently contains the tooling
scaffold, domain models, vault module (path hardening + run lock), privacy
guardrails (canary tokens + fail-closed privacy grep), and the `init` / `validate`
CLI commands. The pipeline itself is not yet built. See the plan for the roadmap.

## Quickstart

> Placeholder — fuller flow lands as later phases build out the pipeline.

```bash
make setup                 # uv sync + pre-commit install

# Create a matter vault OUTSIDE this repo (matter data never lives in the repo):
uv run mootloop init ~/matters/acme-v-widgets \
    --matter-id acme-v-widgets \
    --court "District Court, Hennepin County" \
    --case-number "27-CV-26-1234" \
    --our-side defendant \
    --jurisdiction-state MN \
    --forum state

uv run mootloop validate ~/matters/acme-v-widgets
```

## Guardrails

- **Vault boundary:** matter data never lives in the repo; the vault path is
  asserted outside the repo tree at run start.
- **Secrets:** API keys only in `~/.mootloop/secrets.env` or the OS keychain.
- **Privacy grep:** per-matter canary tokens + denylist, scanned pre-commit and in
  CI; fails closed on anything it cannot read.

## Documentation

- **Plan:** [`docs/plans/2026-07-11-001-feat-mootloop-v1-agentic-litigation-pipeline-plan.md`](docs/plans/2026-07-11-001-feat-mootloop-v1-agentic-litigation-pipeline-plan.md)
- **Brainstorm:** [`docs/brainstorms/2026-07-11-mootloop-brainstorm.md`](docs/brainstorms/2026-07-11-mootloop-brainstorm.md)
- **Agent instructions:** [`AGENTS.md`](AGENTS.md)
- **Third-party components:** [`THIRD-PARTY.md`](THIRD-PARTY.md)

## License

[MIT](LICENSE) © 2026 Damien Riehl
