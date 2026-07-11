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

**Phase 6-7 — Panels, restructure & deliverable export.** The judge panel drives a
restructure pass, and one command turns a finished run into court-formatted
deliverables — clean only when attested.

- **Objection-survival panels** (`models/panels.py`, `panels.py`, `mootloop run
  panels`) — the judge panel's `JudgeOutput` turns fold into a per-objection
  `PanelResult` distribution (survive/total votes, survival rate, reasoning samples)
  and a `PanelReport` written to `runs/<id>/scores/panels/report.json` (the D12
  `PANEL_RESULT` entity).
- **Costed restructure pass** (`stages.py`) — when an objection survives fewer than
  `restructure_threshold` (default 0.5) of the panel, the associate re-enters once per
  affected request (stage `restructure`, a reserved slot) to drop, narrow, or bolster
  the weak objection; the restructured draft becomes the operative one. Requests with
  no weak objection skip the stage (no turn, no cost).
- **Court-formatted deliverables** (`export/`, `mootloop export`) — from a run's
  operative drafts, matter, and served sets: `master.md` with the D7 structure
  (caption, per-set document title, MN Rule 33 interrogatory restatement before each
  answer, `::: {#resp-ID}` anchors, objections with specificity, RFA dispositions with
  the reasonable-inquiry recital, RFP withheld-statements, attorney signature block,
  certificate-of-service stub); `verification.md` (rog sets) with MN's exact perjury
  declaration (unsigned — the client signs on paper); a functional-standard
  `privilege-log.md`; a `strategy-memo.md` (objection strategy, panel survival rates,
  OC attack findings, open risks, spend, citator disclosure); and `audit-log.json`
  derived strictly from the journal + ledger + decisions + attestations (never
  LLM-asserted). No boilerplate general objections; the "subject to and without
  waiving" hedge is blocked by the degeneracy gate (Liguria Foods).
- **DOCX render + watermark + residue scan** (`export/docx_render.py`,
  `export/residue.py`, `config/courts/*.docx`) — pandoc renders a DOCX per served set
  with a court `--reference-doc`; the copy is **DRAFT-watermarked** (`.DRAFT.docx`,
  draft template) until the run is attested AND `export_ready` AND the residue scan is
  clean. The residue scan (raw-zip) rejects any annotation marker, comments part, or
  tracked change. `export_run` is the one shared code path (CLI + `moot-export`
  skill) — a raw call cannot produce an un-attested clean export (plan D3 M12). Where
  pandoc is absent, the DOCX step degrades gracefully and the markdown still ships.

```bash
uv run mootloop run panels ~/matters/acme <run-id>
uv run mootloop export ~/matters/acme <run-id>          # DRAFT until attested + green
uv run mootloop export ~/matters/acme <run-id> --force-draft
```

**Phase 5 — Attorney gates & run modes.** The professional-judgment spine: personas
*propose*, the attorney *approves* — and nothing exports with an unresolved gate.

- **DECISION objects** (`models/decisions.py`, `decisions.py`) — every draft/bolster
  turn derives the P-28 gate set: one **objection-posture** call per request type,
  an **unsupported-assertion** call per attorney-gate item, a **privilege-call** per
  privilege objection, and an **RFA-disposition** call (admit/deny/qualify/lack-of-
  knowledge) per RFA. Generation is idempotent (one decision per logical gate across
  redrafts). Decisions persist append-only to `runs/<id>/decisions/decisions.jsonl`
  with a write-once proposal sidecar; the resolution is a later appended line.
- **Gate taxonomy** (`matter.yaml` `gates:`) — `hard-human` (privilege, RFA,
  attestation) vs `policy-delegable` (objection posture, unsupported assertion). A run
  **cannot finish** while a hard-human gate is open (status `needs_decisions`);
  resolving the last one reopens the run to `finished`. Delegable gates never block
  the finish — they block *export*.
- **decide / attest primitives** (plan D11 parity) — `mootloop decide list|show|
  resolve` (single or `--input` batch), and `mootloop attest` as its own verb.
  Attestation canonicalizes the md-master (line-ending + trailing-whitespace normalize,
  so a whitespace-only edit is a no-op), hashes it plus the citation-ledger head, and
  records append-only. A post-attestation content edit invalidates it (`attest-status`
  → `INVALIDATED`) and re-imposes DRAFT.
- **Gate ledger** (`gate_ledger.py`, `mootloop run gates`) — `runs/<id>/
  gate-ledger.json`, the derived single source of truth for export blocking. It folds
  the per-request turn gates (fabrication, rubric), the citation gate, decisions, and
  attestation into `export_ready(vault, run_id) -> (bool, blockers)`. Phase 7's export
  refuses a clean copy unless it is true.
- **Run modes** (plan D12) — `autonomous` batches every gate into one end-of-run
  review; `gated` pauses at stage boundaries (`run continue` clears the checkpoint);
  `observed` streams `runs/<id>/STATUS.md`, ending with the house `STATE:` marker.
  Resolves `--mode` flag → `matter.yaml` → `autonomous`.

```bash
uv run mootloop run start ~/matters/acme --mode gated
uv run mootloop decide list ~/matters/acme <run-id>
uv run mootloop decide resolve ~/matters/acme <run-id> <dec-id> --action approve --by "Jane"
uv run mootloop attest ~/matters/acme <run-id> --by "Jane"
uv run mootloop run gates ~/matters/acme <run-id>
```

**Phase 4 — Citation & fabrication gates.** The two guardrails that keep fabricated
authority and unsupported facts out of the work product:

- **Fabrication gate** (`gates/fabrication.py`, deterministic, every draft/bolster
  turn) — every `fact_id` a draft uses must exist; every provenance-required assertion
  (a quoted span, a dollar amount, a specific date) must trace to a cited fact's
  statement/provenance or the normalized corpus text; a draft that grounds in nothing
  (no facts *and* no attorney-gate item) fails. Findings are recorded on the turn and
  block at export.
- **Citation extraction** (`citations/extract.py`) — eyecite over cleaned text (plan
  D8: `clean_text` before `get_citations`), classified into case / state-statute /
  federal-statute / regulation / court-rule, with a regex fallback for MN court-rule
  shapes eyecite does not tokenize. Deduped by normalized form.
- **Verification** — a single hardened HTTP layer (`citations/http.py`, the *only*
  module that touches the network) enforces a fixed **egress allowlist** (plan H9),
  builds every request from structured params (never a URL from ingested content), and
  injects the CourtListener token from `~/.mootloop/secrets.env` — never logged.
  Case cites go to **CourtListener** `citation-lookup` (200 → verified, 404 →
  unconfirmed, 400 → invalid, 300 → ambiguous, 429/error → pending; one process-wide
  60/min token bucket, 250-cite chunks); MN statutes/rules to the **Revisor** stable
  URLs; everything else to a **research-request queue** a human fulfills into
  `law/curated/`.
- **Append-only ledger** (`law/verifications.jsonl`, plan D9) — verification status is
  *derived* from the immutable ledger (a persona can never assert "verified"; plan H8).
  The fold is **staleness-aware**: a `verified` entry older than `max_cache_age`
  (default 30d) folds to `pending`, forcing re-verification. A re-run reads the cache
  and makes **zero** network calls until an entry goes stale.
- **Export citation gate** — reads the ledger and blocks unless every citation in the
  operative drafts is verified/curated. Every citation-bearing surface carries the
  standing disclosure: *"Citation currency not checked against a citator
  (KeyCite/Shepard's) — attorney must confirm good-law status."*

```bash
uv run mootloop cite verify ~/matters/acme --run <run-id>   # or --text cites.txt
uv run mootloop research list ~/matters/acme
uv run mootloop research fulfill ~/matters/acme <request-id> --file authority.md --url https://…
```

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

## Demo

A public, **read-only** demo shows the full agentic arc on a synthetic matter —
six personas drafting, attacking, and adjudicating discovery responses through
rubric-gated loops, with the gate ledger, attorney-gate decisions, objection-survival
panels, and finished deliverables all browsable:

- **DEV:** <https://mootloop.dev.openlegalstandard.org>
- **PROD:** <https://mootloop.org> (coming)

The demo run is pre-baked at image build time with a deterministic fake model
provider — zero LLM calls, zero secrets, zero matter-data mechanisms at runtime.
The servers never host real matter data (see [`docs/deploy.md`](docs/deploy.md)).

Run it locally:

```bash
uv sync --extra web
uv run mootloop web bake /tmp/demo
MOOTLOOP_DEMO_VAULT=/tmp/demo uv run uvicorn mootloop.web.app:app
# open http://127.0.0.1:8000
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
