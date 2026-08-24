# Atomic Commitment Ledger — All Historical MootLoop Plans

Created: 2026-08-20

## Purpose and source lock

This is the leaf-level companion to `2026-08-20-plan-completion-audit.md`. The phase
and literal-checkbox ledgers there cover the original implementation and acceptance
lists. This file prevents a D1–D13 or FD-1–FD-10 section from being closed while one
of its individual amendments, controls, thresholds, verbs, or acceptance gates is
still missing.

Source-plan SHA-256 values at reconciliation:

- V1 pipeline: `27a48f99b5eb0914c3a1523fd57641a1f6c7de5b37331599a104476630b87709`
- Demo/deployment: `f4ec91cfe5f7a8e70655ed7d0a04d52655840091ef74f3c98fb23926c24f5d86`
- Hosted cockpit: `66cbba6208589a0596faa2968c8694e108b48ffb7e8cebc7383ee1b4acc1f5bc`

`COMPLETE` means current evidence exists. Every other row names its durable unit,
decision, or successor queue. The source wording remains authoritative when this
short label is less detailed.

## V1 D1–D13 amendments

| ID | Atomic commitment | Disposition |
|---|---|---|
| D1-01 | Core orchestration behind `TurnExecutor`/`LLMProvider`; fake provider tests | COMPLETE |
| D1-02 | YAML-plus-strategy TaskAdapter; new task avoids core changes | PARTIAL — immutable binding complete; synthesis U-12 |
| D1-03 | Uniform Gate protocol with scope, dependencies, and one gate ledger | COMPLETE |
| D1-04 | Agent/persona/task-binding layering contract | COMPLETE |
| D1-05 | Local protocol seams and pinned provenance for copied components | COMPLETE |
| D1-06 | Drafting-specific convergence signals and real rubric deltas | COMPLETE |
| D2-01 | Namespaced `mootloop` Claude Code plugin and verbs | COMPLETE locally — U-11A |
| D2-02 | Disable model invocation for every side-effecting skill | COMPLETE locally — U-11A invariant checks every packaged skill |
| D2-03 | One parameterized judge/juror persona rather than forks | COMPLETE locally — runtime U-07 plus plugin packaging U-11A |
| D2-04 | Least-privilege persona/panel/cite-checker tool sets | COMPLETE for current routes — no persona tools; D-18 legal transport uses a separate authenticated fixed-route identity proved in U-17A |
| D2-05 | Persona-body contract and injected excellence standard | COMPLETE |
| D2-06 | Compact run-skill navigator over durable references | COMPLETE locally — U-11A |
| D3-C1 | Fence all untrusted text as data; content never drives tools/control flow | COMPLETE locally — hostile reimport remains data-only and cannot auto-route ambiguous anchors |
| D3-C2 | Fail-closed OSS scrub plus mandatory rendered human diff; no auto-commit | PARTIAL — U-09 deterministic/PublicText scrub, exact human diff, and pending-only external staging complete; model scrub, publishable-shape allowlist, second human confirmation, and explicit landing primitive U-11B |
| D3-C3 | ID/path/filename realpath-containment hardening | COMPLETE |
| D3-C4 | Google permission read-back and recipient allowlist after export | DECISION-GATED — D-06 |
| D3-H5 | Vault/repo separation and no-follow symlink invariants | COMPLETE |
| D3-H6 | Secrets outside vault/repo, minimal env, runtime redaction | COMPLETE locally / DEPLOYED PARTIAL — U-02; U-17A observes dedicated proxy-secret isolation, while deployed minimal-environment/runtime-redaction proof remains open |
| D3-H7 | Ethical walls and cross-matter learning exclusion | COMPLETE locally — source matter, sharing scope, and per-matter exclusions are launch-filtered |
| D3-H8 | Immutable verification source, chained logs/decisions, full attestation tuple | COMPLETE locally — exact-byte v2 commitment and mutation gates |
| D3-H9 | Fixed legal-host egress allowlist; no content-derived target | COMPLETE for current routes — D-18's separate authenticated CONNECT/443 identity is deployed; arbitrary hosts/paths and cross-identity use fail closed |
| D3-H10 | Defensive DOCX/ZIP/XXE parsing and pinned model/catalog fetches | PARTIAL — protected conversion and hostile local DOCX edit parsing complete; catalog updates U-12 |
| D3-M11 | Per-matter canaries and fail-closed privacy scanning | COMPLETE locally and synthetically deployed — U-02/U-17A; a registered current-matter canary in ingested discovery traversed immutable context assembly and raised `OutboundPrivacyError` before provider subprocess start |
| D3-M12 | Shared export controls for every CLI/skill path | COMPLETE for current sealed exports; extensions U-11A |
| D3-13 | Every confidentiality/attestation error fails closed | PARTIAL — enforced per landing unit |
| D3-14 | `MatterText`/`PublicText` trust conversion through scrub | COMPLETE for current sinks — U-09 shared text joins the U-02/U-05 outbound conversion boundary |
| D4-01 | Flat pointer-plus-summary orchestrator context with scale invariant | OPEN-AUTO — U-16 |
| D4-02 | Global bounded fan-out and one process-wide citation bucket/chunking | PARTIAL — bucket complete; fan-out U-16 |
| D4-03 | Byte-stable prompt prefix/cache breakpoint and cache-write sequencing | OPEN-AUTO — U-16 |
| D4-04 | Batch objections per judge and bound bolster loop | OPEN-AUTO — U-16 |
| D4-05 | Distilled bounded calibrated-judge profile | COMPLETE locally — exact public evidence, holdout error, bounded Judge-only readback |
| D4-06 | Estimate panel term and self-calibrating derail factor | PARTIAL — U-16 |
| D5-01 | Budget tiers vary persona model, iterations, and output caps | PARTIAL — U-16 |
| D5-02 | Cache-aware per-model metering formula | COMPLETE |
| D5-03 | Pinned model IDs and dated price table | COMPLETE |
| D5-04 | API-only panel batching; sequential core never batched | OPEN-AUTO — U-16 |
| D5-05 | Separate orchestrator/convergence and panel model roles | COMPLETE |
| D5-06 | Seat notional-dollar versus API billed-dollar labels | COMPLETE |
| D6-01 | Low default loop caps; increases require measurements | COMPLETE; calibration U-16 |
| D6-02 | External critics/gates; no Associate self-review substitution | COMPLETE |
| D6-03 | Convergence requires score delta and material-change checks | COMPLETE |
| D6-04 | Jury output directional only and never a gate | COMPLETE |
| D6-05 | Judge-specific calibration must be empirically evaluated | COMPLETE locally — deterministic held-out evaluation required before readback |
| D7-01 | Interrogatory completeness, particularity, signature, and MN restatement/oath | COMPLETE for current adapter |
| D7-02 | RFP per-item disposition, specificity, withheld flag/basis, partial production, timing | COMPLETE for current adapter |
| D7-03 | RFA fair disposition and reasonable-inquiry recital; every disposition human-gated | COMPLETE for current adapter |
| D7-04 | Per-document privilege-log fields with categorical option | COMPLETE for current export |
| D7-05 | Filing document structure and no boilerplate general objections | COMPLETE for current adapter/export |
| D7-06 | Sanctions authorities encoded as deterministic/rubric penalties | COMPLETE — Fischer/Liguria/Heller gates plus exact proposition-support proof |
| D8-01 | Court-template DOCX rendering without destructive field rewrites | COMPLETE |
| D8-02 | Bookmark plus sentinel anchor recovery from raw OOXML | COMPLETE locally |
| D8-03 | Parse tracked insertions/deletions and revision metadata directly | COMPLETE locally |
| D8-04 | Google suggestions/comments reimport and large-export handling | DECISION-GATED — D-06 |
| D8-05 | Clean eyecite text with offset back-mapping and bounded tokenizer choice | COMPLETE — original spans plus explicit Aho–Corasick default |
| D8-06 | CriticMarkup representation for pending edits | COMPLETE locally |
| D9-01 | Citation-cache staleness, disclosure, and citator research request | COMPLETE for current clients |
| D9-02 | Retention/destruction-date/litigation-hold close policy and destruction manifest | COMPLETE locally — U-11A |
| D9-03 | Sync-folder/conflicted-copy guard and idle snapshot | COMPLETE |
| D9-04 | Heartbeating stale-lock takeover protocol | COMPLETE locally — turn and backup ownership are renewed and lost ownership fails closed |
| D9-05 | Attestation binds master, ledgers, export set, actor/time; edit invalidates | COMPLETE locally — remote host-writer resistance remains D-09 |
| D9-06 | Content-hash document IDs and whole-vault self-check | PARTIAL — stable ingest IDs and launch snapshots complete; `mootloop verify` reconciliation remains U-11B |
| D9-07 | Append-only fact versions; response and attestation pin fact state | COMPLETE locally — reviewed versions enter immutable launch context and v2 attestation binds its fact commitment |
| D9-08 | Firm profile as append-only reviewed event log | COMPLETE locally — immutable ID-keyed reviewed events plus derived conflict-review view |
| D9-09 | All derived/shared state append-only, content-addressed, timestamped | PARTIAL — enforced per unit |
| D10-01 | Domain models own types; versions, migrations, strict extras | COMPLETE for current persisted models |
| D10-02 | Discriminated GateResult union and exhaustive export handling | COMPLETE |
| D10-03 | LLM provider/fake-provider protocol | COMPLETE |
| D10-04 | Frozen StageContext and write-once StageResult | COMPLETE |
| D10-05 | Sync core; async only at HTTP/fan-out edges | COMPLETE |
| D10-06 | Pure journal fold | COMPLETE |
| D10-07 | One five-layer frozen resolved configuration | COMPLETE |
| D10-08 | Inject copied-component score sources; no hardcoded deltas | COMPLETE |
| D10-09 | Deterministic/replayed/invariant/paid-oracle test tiers | COMPLETE locally — paid lane requires explicit flag and fast CI excludes it |
| D10-10 | Thin Typer adapters and CLI package split as verbs grow | COMPLETE |
| D10-11 | Six-concern learning package across three trust zones | COMPLETE locally |
| D10-12 | `MatterText`/`PublicText` types and scrub producer | COMPLETE for current trust-zone transitions |
| D11-01 | Every action/human gate has structured read/write primitive | PARTIAL — U-11A matrix makes every current/missing row explicit; remaining implementations U-11B/U-12–U-15 |
| D11-02 | Decide/attest/research/facts/manifest/validate CLI verb set | PARTIAL — current verbs are checked; planned capability breadth and whole-vault verify remain U-11B/U-12–U-15 |
| D11-03 | Hard-human versus policy-delegable gate provenance | COMPLETE for current decisions; extensions U-11B |
| D11-04 | Accepted learnings and attorney decisions read back into later prompts | PARTIAL — workflow and immutable next-run readback complete; beneficial attorney verdict U-17C |
| D11-05 | Matter `context.md` read at start and updated at end | COMPLETE locally — exact human-approved sidecar, next-run snapshot, and end-of-run skill prompt |
| D11-06 | JSON sidecars beside human turn/score artifacts | COMPLETE for current artifacts — turn/panel JSON plus exact STATUS/context sidecars; future rooms inherit FD7-16 |
| D11-07 | Emergent in-domain task proof and structured derailment completion signal | PARTIAL — seeded persona-domain regression oracle complete; emergent task proof U-12 |
| D12-01 | Canonical source/derived-artifact vocabulary, trace tree, evidence-pack IDs | COMPLETE locally — U-11A |
| D12-02 | Canonical IDs including response/passages/facts/citations/decisions/learnings | COMPLETE for current models — U-11A adds trace/evidence identity; future stores extend this contract |
| D12-03 | Five-layer config precedence and structural override allowlist | COMPLETE |
| D12-04 | Gate execution-order column and canonical gate names | COMPLETE |
| D13-01 | Pre-first-serve professional-responsibility spine | PARTIAL — immutable context, isolation, integrity, and protected ingest/conversion complete locally; U-11A/U-17 |
| D13-02 | Post-serve learning, breadth, strategy, oracle, and CLI work retained | OPEN/DEFERRED — local learning, strategy, and oracle complete; U-11B and successor queue remain |
| D13-03 | Google/annotated and other explicitly delayed lanes remain visible | DECISION/DEFERRED — D-06/U-03/successor queue |

## Hosted cockpit FD-1–FD-10 amendments

| ID | Atomic commitment | Disposition |
|---|---|---|
| FD1-01 | Personas have read-only file tools; no shell/web tools | COMPLETE locally |
| FD1-02 | Per-turn network jail allows only Anthropic endpoint | COMPLETE locally and synthetically deployed — U-02/U-17A; D-18 legal routes use a separate identity outside persona turns |
| FD1-03 | Per-matter UID/container filesystem isolation | COMPLETE locally and synthetically deployed — U-02/U-17A sibling-path probe |
| FD1-04 | Driver auth, private API network, no Docker socket | COMPLETE for the current deployed worker — authenticated internal ping 200/unauthenticated 401; worker cannot resolve API, has only its two internal networks and five expected mounts, and has no Docker socket |
| FD1-05 | Planted-injection exfiltration gate | COMPLETE — run-visible canary-bearing discovery entered the immutable manifest/corpus snapshot and assembled persona prompt; `HeadlessClaudeProvider.run_turn` raised `OutboundPrivacyError`, and a tripwire proved no subprocess or transport started |
| FD2-01 | Pin audience/RS256/JWKS behavior; reject service tokens on matter routes | COMPLETE |
| FD2-02 | Google uses device flow only | DECISION-GATED — D-06/U-14 |
| FD2-03 | AOP key permissions and non-shared volume | COMPLETE by recorded perimeter evidence |
| FD2-04 | Backups exclude secrets/config token copy; rotation purges history | COMPLETE for the synthetic drill — vault-only backup excludes external secrets/config; U-17A proves wrong-key failure and retired-archive purge without rotating protected keys |
| FD3-01 | Redact Google/OAuth/exact-secret values at every current sink | COMPLETE locally — U-02; future notification sinks U-15 |
| FD3-02 | Runtime canary blocks outbound/notification payloads | PARTIAL — outbound complete locally and synthetically deployed in U-02/U-17A; notification path U-15 |
| FD3-03 | Access audit chained, attestation-bound, fail-closed on downloads, stronger sink | COMPLETE locally — stronger remote sink remains D-09 |
| FD3-04 | Secret ntfy topic, content-free digest, separate Gmail/Drive credentials | OPEN/DECISION — U-15/D-06 |
| FD4-01 | Auto-derived board edits are immediately visible and journaled | OPEN-AUTO — U-13 |
| FD4-02 | Only attorney-approved/curated nodes enter prompts | COMPLETE at injection boundary; board producer U-13 |
| FD4-03 | Untrusted auto-findings remain `needs_review` until promotion | OPEN-AUTO — U-13/U-14 |
| FD4-04 | Injected board text is fenced and provenance-tagged | COMPLETE at injection boundary; board producer U-13 |
| FD5-01 | Synthesis limited honestly to discovery-family pipeline shape | OPEN-AUTO — U-12; broad shape deferred |
| FD5-02 | Generic synthesized adapter, vault-aware binding, hash/lineage sidecars | PARTIAL — immutable vault binding complete; synthesis U-12 |
| FD5-03 | First-class pause/resume status/events | COMPLETE |
| FD5-04 | Priority queue, slot release, heartbeat/visibility reclaim | COMPLETE locally — U-11A closes shutdown/capacity attempt accounting |
| FD5-05 | SSE read-only tail and stepwise driver loop | COMPLETE |
| FD5-06 | Thin Next BFF as sole verified surface | COMPLETE locally; deployed unauthenticated route boundary proved in U-17A; authenticated journey still pending human Access login |
| FD5-07 | Stateless web redeploy; driver drain/reclaim contract | COMPLETE for the current single-worker contract — U-17A proves PID 1, clean stop/recreate, in-flight exact-item release with refunded attempt, one boot reclaim, unchanged completed-turn/spend baseline, and no duplicate work; multi-worker failover extension remains U-15 |
| FD6-01 | Driver-coordinated encrypted off-box backup and restore drill | PARTIAL — U-17A proves byte-identical encrypted same-host synthetic restore and key retirement; an independently approved encrypted off-box backup destination and restore drill remain open. D-09's signed-head integrity ledger does not close this requirement. |
| FD6-02 | Close inventory/source binding/anonymized audit retention | COMPLETE locally — U-11A implements D-14 for its new stores and a complete close manifest |
| FD6-03 | Atomic board edit/changelog fold and typed optimistic conflict | OPEN-AUTO — U-13 |
| FD6-04 | Write-ahead idempotent spend intent with billing tag and conservative cap | COMPLETE |
| FD6-05 | Watcher token/cursor/reconcile/idempotent-notification recovery | OPEN/DECISION — U-14/D-06 |
| FD6-06 | Isolated upload staging, atomic promotion, GC, and locked writers | OPEN-AUTO — U-14 |
| FD7-01 | `matters list` parity row | COMPLETE |
| FD7-02 | `tasks synthesize` parity row | OPEN-AUTO — U-12 |
| FD7-03 | hard-human `tasks lock` with approver/rubric hash | COMPLETE |
| FD7-04 | policy-delegable `tasks rubric edit` with provenance | OPEN-AUTO — U-12 |
| FD7-05 | board add/edit/remove/show parity rows | OPEN-AUTO — U-13 |
| FD7-06 | board curate parity row | OPEN-AUTO — U-13 |
| FD7-07 | board revert parity row | OPEN-AUTO — U-13 |
| FD7-08 | board changelog-list parity row | OPEN-AUTO — U-13 |
| FD7-09 | notification feed-list parity row | OPEN-AUTO — U-15 |
| FD7-10 | suggestions list/accept/dismiss with logged dismissal | COMPLETE for RFP production review — U-08; watched-source suggestions remain U-14 |
| FD7-11 | hard-human/policy-bounded run failover authorization | OPEN-AUTO — U-15 |
| FD7-12 | needs-triage reuses manifest privilege/role mutation | COMPLETE for local ingest — U-14 reuses this primitive for watched uploads |
| FD7-13 | export-link writes access audit | COMPLETE |
| FD7-14 | connectors list/add-folder/remove; OAuth consent human-only | DECISION/OPEN — D-06/U-14 |
| FD7-15 | notification mute and quiet-hours parity rows | OPEN-AUTO — U-15 |
| FD7-16 | Every new store is durable, close-registered, listable/showable | PARTIAL — U-11A registrations/invariant complete; each U-11B–U-15 store must add its own surfaces |
| FD7-17 | Automated BFF-thin invariant | COMPLETE locally |
| FD8-01 | OpenAPI-generated TypeScript plus CI drift gate | COMPLETE locally |
| FD8-02 | `openapi-fetch` and bounded domain modules | COMPLETE for FE-2; extend U-12–U-15 |
| FD8-03 | Same-origin Access cookie; no Authorization threading | COMPLETE |
| FD8-04 | TanStack Query is server truth; no mirrored server state | COMPLETE for FE-2 |
| FD8-05 | Zustand persistence only for drafts | COMPLETE for FE-2 |
| FD8-06 | Backend discriminators and exhaustive TypeScript unions | COMPLETE for FE-2 |
| FD8-07 | Zod parses every SSE event with pinned tests | COMPLETE |
| FD8-08 | Fetch-event-source login detection and shared session-expired error | COMPLETE |
| FD8-09 | Hierarchical query-key factories | COMPLETE |
| FD8-10 | Decide may be conflict-aware optimistic; attest never optimistic | COMPLETE |
| FD8-11 | Module-scoped JWKS and matcher discipline | COMPLETE |
| FD8-12 | Typed MSW/unit tests now; browser tests only for unreproducible seams | COMPLETE for FE-2; U-17A authenticated mobile seam waits for human Access login |
| FD8-13 | Tokenized/generalized board component adaptations | OPEN-AUTO — U-13 |
| FD9-01 | Case-file/pleading-spine navigation and mobile docket tabs | PARTIAL — remaining rooms U-12–U-15 |
| FD9-02 | Serif argument voice and mono record voice | COMPLETE for FE-2 |
| FD9-03 | One restrained inking motion language | COMPLETE for FE-2; extend future rooms |
| FD9-04 | Coverage-seal vocabulary for board states | OPEN-AUTO — U-13 |
| FD9-05 | Input-shape on-ramp with three honest lanes | PARTIAL — U-12 |
| FD9-06 | Two-step RFA binding ceremony | COMPLETE |
| FD9-07 | Certify-and-release download colophon | COMPLETE |
| FD9-08 | Linearized accessible mobile board | OPEN-AUTO — U-13 |
| FD9-09 | Avoid banned SaaS/legal-cliché visual patterns | PARTIAL — enforce in U-12–U-15 |
| FD9-10 | Durable room-by-room frontend direction document | COMPLETE locally — U-11A |
| FD10-01 | First-live sequence FE-0 → FE-2.5 → protected seed/run | PARTIAL — U-17A's autonomous synthetic infrastructure proof is complete; the authenticated mobile journey and protected U-17B clean proof remain |
| FD10-02 | Post-live FE-3 → FE-6 sequence | OPEN-AUTO — U-12–U-15 under D-10 |
| FD10-03 | Dropbox/OneDrive/Web Push/pipeline-shape/multiplayer stay deferred | DEFERRED — successor queue |
| FD10-04 | TaskSpec fields land only as consumed | COMPLETE for current TaskSpec; extensions enforced in U-12 |
| FD10-05 | `edited_by` provenance retained | OPEN-AUTO — U-12/U-13 |

## Closure rule

An umbrella D/FD row in the main audit may be changed to `COMPLETE` only when every
atomic child here is `COMPLETE`, or the user has explicitly reclassified that child as
decision-gated/deferred. Implementation units must cite the child IDs they close in
their execution handoff.
