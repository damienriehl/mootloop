# MootLoop Frontend — FOLIO-Grounded Task Initiation & Oversight

**Date:** 2026-07-12
**Participants:** Damien Riehl, Claude (Fable)
**Origin question:** "Do we have a frontend to begin a task?" (Answer: no — CLI/skills/read-only demo only.)
**Status:** Captured — all questions resolved

## What We're Building

A **hosted web app at `mootloop.damienriehl.com`** (Hetzner, behind Cloudflare Access) — the attorney's cockpit for real matters, usable from phone/Chromebook/laptop. Six rooms, all v1:

1. **Matter dashboard** — matters, deadlines, run history, spend.
2. **Begin-task on-ramps** (three blended, per UX best practice):
   - **Picklist wizard** — search-first catalog over FOLIO **Litigation Document** (668 concepts; Interrogatories/RFP/RFA/Motions/Appellate docs are real nodes), faceted by **areas_of_law** chips (130 — facet, not tree), tagged with UTBMS L-codes;
   - **System suggestions** — LLM proposes next tasks from matter state (deadlines, last served/filed, gaps), each grounded to a FOLIO IRI;
   - **Freeform** — user types intent; resolve-after-generate snaps it to catalog concepts (alea-intake `concept_resolver` pattern) or flags unmapped.
3. **Strategy board** — the "it depends" permutation explorer rooted on FOLIO **Litigation Objectives** (1,847 — FOLIO's own "elements to be proven or disproven"): parallel axes Claims (1,423) × Defenses (218, incl. the full named affirmative-defense catalog) × Elements × Remedies × Burdens × Standards of Review. Pick a claim → adjacency traversal surfaces related defenses → React-Flow DAG renders the neighborhood. **The LLM extrapolates what FOLIO deliberately omits** (ordered per-claim element checklists, jurisdiction-specific standards, fact-to-element mapping), every proposal snapped back to an IRI or flagged unmapped. Attorney curates the board; the curated board feeds run prompts and fact/element targets.
4. **Run cockpit (human ON the loop)** — live pipeline strip, per-request gate chips, iteration timeline, spend meter, panel distributions; streams from the journal.
5. **Decision inbox (human IN the loop)** — the propose-then-approve queue fronting the existing DECISION primitives; **inbox + badge semantics** (run continues where it can; pauses only when nothing can proceed). Attest is a distinct, prominent act.
6. **Export & audit room** — deliverables, DRAFT/clean state, the reasoning audit trail (journal → trace tree → per-passage attribution → citation ledger), concept-grounded (FOLIO IRIs) end to end.

## Why This Approach

- **Hosted-with-trust supersedes local-only** (revises brainstorm D-14's deployment posture, not its principle): Damien is both admin and client, so a hardened server he controls satisfies the confidentiality duty; remote control from any device is the payoff. The public demo tier stays read-only/synthetic and separate.
- **FOLIO as skeleton, LLM as flesh** is exactly how the taxonomy is built: it provides claim/defense/remedy *nodes* and generic element primitives (Causation, Damages, Intent, Scienter) but not per-claim checklists — the extrapolation seam is by design. Grounding every LLM proposal to an IRI gives the fabrication/audit machinery a concept-level trail.
- **Stack from the house's best parts** (verified from package.json, not memory): **ontokit-web's chassis** (Next.js, React 19, Radix, Tailwind v4, TanStack Query) + **folio-mapper's components** (React Flow DAG, CandidateTree, DetailPanel, typed FOLIO client) + **folio-enrich's data model** (ConceptMatch span→candidates→confirm/reject) + **alea-intake's grounding engine** (concept_resolver, adjacency, issue_spot resolve-after-generate) — over the existing MootLoop FastAPI web module.

## Key Decisions

| # | Decision | Choice |
|---|----------|--------|
| F-1 | Hosting | **mootloop.damienriehl.com** on the existing Hetzner box; damienriehl.com zone is on Damien's Cloudflare (DNS automatable) |
| F-2 | Access control | **Cloudflare Access** (free tier; Google login + 2FA from any device; server firewall accepts only Cloudflare edge + Tailscale) + app-level session as defense-in-depth |
| F-3 | Matter tier | **Same box, hardened**: dedicated OS user, 0700 vault perms, own Coolify project, no public route except through Access, encrypted+versioned off-box backups. Real-matter tier strictly separate from the public demo app |
| F-4 | Engine | **Headless Claude Code CLI on the server**, authenticated to Damien's Max plan (one-time `claude setup-token`); the UI's Start actually starts; **Max tokens, zero API spend**; fallback = stage the run and drive from any Claude Code session. OAuth token = crown-jewel secret (0600, dedicated user) |
| F-5 | On-ramps | All three blended: picklist wizard + system suggestions + freeform intent, converging on the same FOLIO-grounded task spec |
| F-6 | Task catalog | FOLIO **Litigation Document** (search-first + lazy tree; polyhierarchy → IRI is canonical key, multiple breadcrumbs) + areas_of_law facet chips + UTBMS tagging; cross-ref Litigation Practice (66) for verb framing |
| F-7 | Strategy board | Rooted on **Litigation Objectives**; claims×defenses×elements×remedies axes; adjacency-driven DAG (React Flow); LLM extrapolation with resolve-after-generate IRI grounding; attorney curation gates what enters run prompts |
| F-8 | HITL | Inbox + badge; run continues where unblocked; hard-human gates still block completion (existing semantics); attest is a separate deliberate act |
| F-9 | HOTL | Live cockpit streaming from the journal (real-time) + export/audit room over gate-ledger, audit-log, trace tree (after-the-fact, reasoning visible) |
| F-10 | Stack | Next.js/React 19/Radix/Tailwind/TanStack Query chassis (ontokit-web conventions) + folio-mapper components (React Flow DAG, FOLIO browser) + FastAPI backend extension; courtroom-ledger design identity |
| F-11 | V1 scope | All six rooms, single-tenant (Damien); multi-user/firm tier waits for SDK extraction |
| F-12 | Security workstream | First-class: threat-model the hosted matter tier before build (Cloudflare Access policy, firewall, secrets, backup encryption, audit logging of access, CSRF/session hardening) — built together with Damien |

## Resolved Questions

- **Do we have a frontend?** No — this brainstorm defines it.
- **Hosted vs local?** Hosted with hardening (F-1..F-3) — admin and user share one trust domain.
- **Max plan vs API?** Headless CLI on the server preserves Max-plan economics (F-4).
- **Which FOLIO branches?** Litigation Document for catalog; Litigation Objectives for skeleton; areas_of_law as facet only (research-verified against the 18,325-class OWL).
- **Which frontend property to build on?** ontokit-web chassis + folio-mapper components + folio-enrich data model + alea-intake grounding (F-10).
- **HITL style?** Inbox + badge (F-8).

## Open Questions

None — all resolved above.

## Next Step

`/ce:plan` — expected to cover: security hardening plan (F-12) before feature build; backend API extension (task specs, strategy-board persistence, suggestion engine); headless-driver design; FOLIO service layer (folio-python deterministic lane); the six rooms in build order (likely: cockpit+inbox first — they front existing primitives — then wizard/board, then dashboard/audit polish).
