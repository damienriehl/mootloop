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

**Phase 3 — Convergence, rubrics, budget.** On top of the Phase 1 deterministic
front-end and the Phase 2 orchestrator (the stepwise, journal-folded persona
pipeline), this phase makes loops *terminate on quality* and *respect a budget*:

- **Locked rubric** — `rubrics/discovery-responses-v1.0.yaml` encodes the discovery
  practice checklist (plan D7) as versioned criteria of two kinds. *Presence*
  criteria (per-request disposition, objection specificity, RFP withheld-statement,
  RFA disposition + reasonable-inquiry recital, MN interrogatory restatement, no
  boilerplate objections, no "subject to and without waiving" hedge) are checked
  deterministically in `gates/completeness.py` and never sent to a judge. *Correctness*
  criteria are judge-scored 0-5. The rubric is **content-hash locked** — changing it
  requires a new version file, never an in-place edit.
- **Convergence** — a single rubric judge scores each partner-loop round; the loop
  stops only when the draft **stopped improving AND stopped changing AND is complete**
  (rubric-delta floor + token-level material-change floor + presence-coverage floor),
  or the iteration cap is hit (plan D6). No embeddings — material change is a
  deterministic `difflib` ratio.
- **Final rubric gate** — after bolstering, a decorrelated **3-judge** panel (distinct
  lenses; median-per-criterion, weighted) gates the response against a threshold.
- **Budget** — a dated price table meters every call with the four-bucket cache-aware
  formula; tiers move the persona/judge/rubric/cite model (plan D5). `mootloop run
  estimate` prints a pre-run range + per-stage breakdown; `run status` shows live
  tokens + a notional `$`-equivalent; a `hard_cap_usd` triggers a **graceful
  checkpoint** — a gaps report + a `capped` run that `mootloop run raise-cap` reopens.

Earlier phases remain: **corpus ingestion** (`mootloop ingest`), the **discovery
parser** (`mootloop requests parse`), and the append-only **fact repository**
(`mootloop facts add` / `list`). A fully synthetic MN breach-of-contract matter lives
in `fixtures/synthetic-matter/` and runs the whole path in CI. Live model calls are
not wired in v1 — the `FakeLLMProvider` drives every run.

### Budget: estimate, meter, cap

```bash
# Pre-run cost range + per-stage breakdown (notional $, plan mode):
uv run mootloop run estimate ~/matters/acme --tier moderate

# Live spend (tokens + notional $-equivalent) folded from the journal:
uv run mootloop run status ~/matters/acme <run-id>

# If a run hits matter.yaml's budget.hard_cap_usd it checkpoints to `capped` and
# writes deliverables/gaps-<run-id>.md. Raise the cap and resume:
uv run mootloop run raise-cap ~/matters/acme <run-id> --to 120
uv run mootloop run drive ~/matters/acme <run-id> --fake
```

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
