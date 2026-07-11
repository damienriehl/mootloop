# MootLoop — Brainstorm

**Date:** 2026-07-11
**Participants:** Damien Riehl, Claude (Fable)
**Source material:** "The Agentic Law Firm" (Riehl), LawLoop PRD outline
**Status:** Captured — pending open-question resolution

## What We're Building

**MootLoop** is an agentic law firm simulator: a multi-persona system that drafts, attacks, and adjudicates legal work product before a human lawyer reads a page. It implements the article's full arc:

1. **Associate Agent** — ingests the matter corpus, spots issues, builds the claims × jurisdictions × elements matrix, maps facts to elements, flags evidentiary gaps, drafts work product.
2. **Partner Agent** — senior-litigator review; sends work back with specific critiques. The Associate↔Partner loop runs until a configurable diminishing-returns threshold.
3. **Opposing Counsel Associate + Partner Agents** — receive the polished product and build the strongest case against it, refined through their own internal loop ("polished rocks").
4. **Judge Agents (panel of N)** — each independently rules on the identical record; results form a probability distribution that drives restructuring (lead with winners, demote losers).
5. **Jury Agents (panel of N)** — v1: persuasion panel (lay comprehension, credibility, resonance scoring). Later: deliberating mock-verdict panel with damages ranges and Monte Carlo simulation across panel compositions.

**Human ON the loop:** every persona turn, critique, revision, and ruling is persisted as a readable, traceable artifact — the user can audit the full iteration history and intervene at any point.

**v1 deliverable (real-case proving ground):** Discovery responses — interrogatory answers, RFP responses, RFA admit/deny/qualify — for Damien's live lawsuit (bench trial; complaint, answer, and outgoing discovery already drafted by hand). The architecture treats "discovery responses" as one *matter task* among many; Complaint, Answer, Outgoing Discovery, motions, and eventually the full FOLIO legal-task catalog run through the same persona pipeline.

## Why This Approach

- **Claude Code-native now, extract later (hybrid).** V1 is a repo of agent definitions, persona prompts, workflows, and skills run inside Claude Code — Fable orchestrates, Opus subagents perform persona work. This proves the loops on a real case fastest, and "seat-based plan" support falls out for free (runs on Damien's existing Claude plan). Once persona prompts and iteration logic are validated, extract into an Agent SDK app with its own UI and API-key budget metering.
- **Thin full pipeline first.** V1 runs every enabled persona end-to-end on one discovery-response task with loops capped low — proving the whole arc immediately — then deepens each persona. *Pipeline strategy is user-selectable:* `thin-full` (default), `deep-core` (perfect Associate↔Partner first), `adversarial-first` (attack existing human drafts — immediately useful since Damien's drafts already exist).
- **Me-first, OSS-ready.** Built for one real lawsuit as the crucible, but MIT-licensed and structured from day one so other lawyers can adopt it.
- **Reuse over rebuild.** alea-intake already implements much of the Associate's front half (issue-spotting, fact→element mapping, gap analysis, question generation, deadline engine, LLM cost metrics); folio-enrich handles multi-format ingestion + ontology tagging; folio-insights holds litigation tactics from Damien's trial-advocacy books; the evidence-pack/lane-watch tooling provides the judging harness pattern.

## Key Decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Name / repo | **MootLoop** / `mootloop` — verified clean on GitHub (repos + org name) and PyPI; **mootloop.org registered by Damien 2026-07-11**; mootloop.com and mootloop.ai appeared unregistered at check time. LawLoop rejected (existing legal SaaS + GitHub org); FirmOS rejected (existing company, 43 repos, oversells scope); LegalLoop rejected (LegalLoop.ai ships a Claude Code legal skill + MCP connector) |
| 2 | Audience | Damien first, OSS-ready (MIT) from day one |
| 3 | Form factor | Hybrid: Claude Code-native v1 → Agent SDK extraction later |
| 4 | Orchestration | Fable orchestrates/judges; Opus subagents perform persona work |
| 5 | Personas | All six, individually enable/bypass-able via config |
| 6 | Pipeline strategy | `thin-full` default; `deep-core` and `adversarial-first` user-selectable |
| 7 | Traceability | Every iteration persisted and human-auditable (human on the loop) |
| 8 | v1 task | Discovery responses (rogs + RFPs + RFAs), real case, no deadline pressure — optimize for quality |
| 9 | Task taxonomy | FOLIO is the backbone for legal tasks/services; agents use FOLIO MCP (reasoning lane) + folio-python/folio-api (deterministic lane); folio-enrich for ingestion/tagging; folio-insights as tactics KB |
| 10 | Law sources (tiered) | (a) curated matter corpus = authoritative/citable; (b) free APIs (CourtListener/RECAP, GovInfo, state sites) = verification + expansion; (c) Westlaw/Lexis manual bridge = system emits research requests, human fulfills; (d) model knowledge + web search = hypothesis generation only. **Citation-verification gate:** nothing is cited in work product until verified against (a) or (b) |
| 11 | Iteration control | User-configurable max iterations per loop AND configurable diminishing-returns criterion (e.g., rubric-scored improvement delta below threshold) |
| 12 | Budget tiers | No-budget / moderate / low, each with dollar estimates before a run; seat-based (plan) usage supported natively in v1 via Claude Code |
| 13 | Jury Agent | Both roles, phased: persuasion panel in v1; deliberating verdict panel + Monte Carlo panel-composition simulation later. Built even though Damien's case is a bench trial |
| 14 | Data separation | Public OSS repo contains **zero matter data**. All matter data lives in a **matter vault** outside the repo (path-configured, e.g. `~/Matters/<matter-id>/`), with `matters/` gitignored and a pre-commit guard blocking matter-vault content from ever entering the repo. Damien's case = first vault; Anthropic-plan trust level acceptable for it |
| 15 | Judge modeling | Generic diverse panel by default + optional **calibrated-judge persona** built from the assigned judge's published opinions (US-lawful; jurisdiction warning for non-US users) |
| 16 | Vault + cloud | Canonical vault = **local plain directory**. Cloud in two optional layers: (a) user-managed Drive/Dropbox desktop sync of the vault dir for backup/multi-device (zero MootLoop code); (b) first-class **ingestion connectors** (Drive/Dropbox via MCP/API) that pull client documents into the vault. Cloud is never the live store the pipeline reads from |
| 17 | AI-use audit trail | Built-in: disclosure-ready export (which agents produced what, every citation's verification status) derived from the traceability logs — future-proofs against AI-disclosure standing orders |
| 18 | Cost display | Every run pre-estimates and post-reports **tokens + API-price $-equivalent** — plan users see notional cost, API users see real cost, one mechanism |
| 19 | Run interaction model | Per-run setting, all three modes: **autonomous** (run to completion, review after), **gated** (configurable checkpoints, e.g. before the Opposing Counsel round), **observed** (live persona-activity view with interrupt, lane-watch style) |
| 20 | Deliverable formats | **Markdown master** (diffable, drives iteration tracing) rendered to: court-ready **DOCX** (caption/format template per court), **Google Doc export** (co-party/co-counsel collaboration), companion **strategy memo** (objection strategy, risk flags, judicial-panel odds, opposing-counsel findings), and an **annotated draft** (per-passage confidence, citation-verification status, persona attribution — annotations strip on export) |
| 21 | Success criteria + edit-learning | Rubric gates inside loops (drive convergence) + **hand-draft benchmark** (pipeline re-runs the tasks Damien already drafted; success = equal-or-better by his judgment) + **edit-learning loop**: user edits output → system analyzes diffs → learnings improve (1) this matter, (2) firm preferences, (3) area-of-law playbooks, compounding across matters and users |
| 22 | Persona taste | Generic top-tier practice by default; **optional style corpus** (user's prior briefs/edits) calibrates personas when available |
| 23 | Learning storage tiers | Matter learnings → that matter's vault; firm learnings → private **firm profile** directory outside the OSS repo, shared across matters; area-of-law learnings → scrubbed of client facts, eligible to contribute back to OSS as shared playbooks |
| 24 | Multi-user | **Shared firm profile** from v1 (private shared store with merge semantics for concurrent learnings) + **co-counsel review lane**: Google Doc comments ingested back as edit-learning input |
| 25 | Professional-responsibility guardrails | All four, built-in: (a) **no-fabrication enforcement** — citation-verification gate hard-blocks unverified authority from deliverables; (b) **human-signoff attestation** — DRAFT watermark until an explicit "reviewed by [name]" attestation at export (Rule 5.3-style supervision); (c) **UPL framing** — OSS docs/outputs frame MootLoop as a lawyer's tool, not legal advice; (d) **confidentiality preflight** — pre-run check that matter data isn't leaking outside the vault and the LLM endpoint is the approved one |
| 26 | Case corpus status | Damien's case materials are already collected in one organized digital folder — v1 ingestion = point folio-enrich at it to populate the first matter vault |

## Resolved Questions

*(moved here as answered during brainstorm)*

- **Which deliverable first?** Discovery responses — earlier artifacts already hand-drafted.
- **Where does law come from?** All four sources, tiered with a verification gate (Decision 10).
- **What does the Jury Agent do?** Persuasion panel now, verdict panel + Monte Carlo later (Decision 13).
- **Build order?** Thin full pipeline, with strategy selectable (Decision 6).

- **Judge modeling?** Generic panel + optional calibrated real-judge persona (Decision 15).
- **Vault shape and cloud storage?** Local plain directory canonical; cloud as optional sync (user-managed) and ingestion connectors (Decision 16).
- **AI-use audit trail?** Built-in, disclosure-ready (Decision 17).
- **Cost on a seat-based plan?** Tokens + $-equivalent everywhere (Decision 18).
- **Interaction during runs?** All three modes, per-run configurable (Decision 19).
- **What does the deliverable look like?** Markdown master → DOCX + Google Doc + strategy memo + annotated draft (Decision 20).
- **How do we know it's good?** Rubric gates + hand-draft benchmark + edit-learning loop (Decision 21).
- **Persona taste?** Generic excellence default, optional style corpus (Decision 22).
- **Where do learnings live?** Three tiers: matter vault / private firm profile / scrubbed OSS playbooks (Decision 23).
- **Multi-user?** Shared firm profile + co-counsel review lane in v1 (Decision 24).
- **Ethics guardrails?** No-fabrication gate, signoff attestation, UPL framing, confidentiality preflight — all built-in (Decision 25).
- **Case materials?** Already organized in one folder; ingestion points at it (Decision 26).

## Open Questions

None — all resolved above.

## Next Step

`/ce:plan` to structure the v1 build.
