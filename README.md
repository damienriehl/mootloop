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

**Phase 1 — Ingestion, requests, facts.** On top of the Phase 0 scaffold (domain
models, vault path-hardening + run lock, canary/privacy-grep guardrails, `init` /
`validate`), this phase adds the deterministic front of the pipeline:

- **Corpus ingestion** — `mootloop ingest` walks a source folder, content-addresses
  every document (`doc-<sha256[:16]>`, stable across re-ingest), copies originals,
  and normalizes `.txt` / `.md` / `.docx` / `.eml` to `corpus/normalized/`. PDFs and
  unknown types surface as `needs_conversion`; symlinks/unreadable files fail closed.
  Role/privilege tags apply non-interactively via a `tags.yaml` glob map.
- **Discovery parser** — `mootloop requests parse` turns served interrogatories /
  RFPs / RFAs into numbered work items with canonical opponent-owned IDs (`ROG-3`,
  `RFP-12`, `RFA-7`, subparts `ROG-3(a)`); numbering gaps become warnings, never
  silent drops. Deterministic, no LLM.
- **Fact repository** — `mootloop facts add` / `list` over an append-only,
  content-addressed, versioned fact log (`facts/facts.jsonl`) with corpus provenance.

A fully synthetic MN breach-of-contract matter lives in `fixtures/synthetic-matter/`
and runs the whole path in CI. The persona pipeline itself is not yet built — see the
plan for the roadmap.

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
