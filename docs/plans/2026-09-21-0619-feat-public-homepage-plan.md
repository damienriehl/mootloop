---
title: MootLoop Public Homepage - Plan
type: feat
date: 2026-09-21
artifact_contract: ce-unified-plan/v1
product_contract_source: ce-plan-bootstrap
execution: code
---

# MootLoop Public Homepage - Plan

## Goal Capsule

**Objective:** Visitors understand how MootLoop challenges legal work, find a relevant demo, and know how to use the project locally.

**Means:** Extend the public reader with an accessible static homepage (KTD1).

**Authority:** Current user instructions, AGENTS.md, Product Contract, then implementation decisions. The executing agent owns validation, PR, merge and deployment under the user's standing authorization. Stop for a demonstrated scope conflict or inaccessible required infrastructure; preserve unrelated working files.

---

## Product Contract

### Summary

Give MootLoop a public front door that explains its adversarial legal workflow and offers direct paths into the existing demo collections. Show practical features and local installation guidance, with clear source-code links.

### Problem Frame

The current root redirects straight to a demo catalog. New visitors have little context for the product, its intended audiences, or the distinction between a prepared demonstration and work on their own materials.

### Key Decisions

- **Local installation for personal work** (session-settled: user-directed — chosen over BYOK: public key handling adds unnecessary complexity). Governs R4.
- **Counterfactual real-case demos** (session-settled: user-directed — chosen over simply retelling outcomes: explore alternative strategies and their possible consequences). Governs R3.

### Requirements

#### Understanding the product

- R1. The root page explains drafting, opposing review, revision, rubric assessment and the attorney's role in plain language.
- R2. The page gives lawyers, law students, in-house counsel, legal operations, developers and interested nonlawyers a relevant reason to explore MootLoop, without promising legal outcomes or verified accuracy.

#### Finding and using examples

- R3. Visitors can reach all demos and each of the three existing collections: five fictional litigation matters, five public-record counterfactuals and ten business questions. Prepared examples are identified as educational scripts; hypothetical alternatives are not predictions.
- R4. Visitors can follow local installation instructions without submitting documents, credentials or execution requests to the public site.
- R5. Source-code links point to the actual MootLoop repository and its relevant components, clearly labeled. Do not imply separate repositories where the project is a monorepo.

#### Access and continuity

- R6. The homepage works without JavaScript, supports keyboard navigation and narrow screens, and respects system light/dark preferences.
- R7. Existing demo routes, revision downloads, navigation and read-only security boundaries retain their behavior. The homepage remains readable when the demo release is unavailable; library errors remain explicit.

### Scope Boundaries

Existing demo content is the published release. New cases, public execution, BYOK, account registration and changes to the protected application are outside this homepage work. Automated persona walkthroughs are evidence of simulated UAT, not human research.

---

## Planning Contract

### Key Technical Decisions

- KTD1. Serve a dedicated HTML file at `/` through the existing FastAPI FileResponse pattern. This avoids a second framework and keeps the page independent of catalog availability (R6, R7).
- KTD2. Use ordinary links to the library's existing `collection` query parameter, with values `synthetic`, `public-record` and `business`. Collection links avoid brittle editorial bindings to individual revisions (R3).
- KTD3. Add homepage-scoped CSS and preserve existing shared styles. Use system serif and sans-serif fonts, no external assets or JavaScript, and native anchors for all actions (R6).
- KTD4. Link the main repository, Python source directory, frontend directory and local-use guide with labels that identify their purpose (R5).
- KTD5. Ship through the current pinned demo Docker build and authorized SSH/Compose deployment path. Verify the active container and capture its current configuration before changes; deploy the same verified image to development and production (R7).

### Assumptions

A restrained legal-workbench design suits the existing viewer: cool white `#f7f8f6`, raised white `#fdfdfc`, ink `#1d2226`, muted ink `#4a5258`, oxblood `#7c3030`, and pale rule `#d4d7d2`. Charter/Georgia display type pairs with system sans-serif body text. The memorable element is a visible draft/challenge/revision exchange, explicitly labeled as an illustration. A two-column opening collapses into a single reading sequence on mobile. Keep demo choices larger and more prominent than feature descriptions.

Only one project repository is evidenced by Git remotes and the installation guide; component links fulfill the requested source access without inventing companion repositories.

### Research and Dependencies

- `src/mootloop/web/app.py`: root redirect, static FileResponse routes and strict response headers.
- `src/mootloop/web/static/library.js`: collection query filters and same-page anchor navigation.
- `src/mootloop/web/static/library.css` and `styles.css`: responsive layout, type and system theme tokens.
- `README.md` and `docs/demos/local-use.md`: supported personas, gates, local installation and prepared replay boundaries.
- `docs/reviews/2026-09-20-demo-persona-uat.md`: existing persona flows to preserve.
- `Dockerfile`, `config/demos/release.json` and `tools/smoke_demo_runtime.py`: pinned public distribution and runtime verification.

### Risks and Rollout

Main-branch merges can regenerate Coolify configuration independently of the active Compose service. Select the actual active service and preserve its current image and routing for rollback. Confirm public root and demo readiness after each deployment. Never copy real-case sources or external release contents into Git.

---

## Implementation Units

### U1. Build the public homepage and navigation

**Goal:** Deliver the complete visitor journey from explanation to demos or local setup.

**Requirements:** R1–R7. **Dependencies:** None.

**Files:** `src/mootloop/web/app.py`, `src/mootloop/web/static/home.html`, `src/mootloop/web/static/home.css`, `src/mootloop/web/static/library.html`, `tests/integration/test_web_api.py`.

**Approach:** Apply KTD1–KTD4. Create the opening illustration, workflow explanation, collection links, audience use cases and source/local-use section. Make the library wordmark return home. Keep all homepage copy independent of source filings.

**Patterns to follow:** Existing FileResponse routes, library landmarks and shared theme variables.

**Test scenarios:**

- Request `/` without a public release: receive readable HTML and local/demo links, with the current security headers.
- Follow each collection link: the catalog opens with the intended filter and count.
- Navigate with keyboard and fragment links: focus and destinations remain usable.
- Follow the library wordmark home and return to a demo: existing section navigation still works.

**Verification:** HTTP contract checks pass; desktop, mobile and no-script inspection show a usable homepage and working destinations.

### U2. Verify visitor journeys and publish

**Goal:** Make the reviewed homepage available on both public hosts.

**Requirements:** R1–R7. **Dependencies:** U1.

**Files:** `tools/smoke_demo_runtime.py`, `README.md`, `docs/demos/local-use.md`, `docs/reviews/2026-09-21-homepage-uat.md`.

**Approach:** Extend container smoke coverage for root HTML. Update entry-point documentation. Run simulated persona journeys, resolve observed defects, and follow KTD5 with recorded rollout evidence.

**Test scenarios:**

- A lawyer finds fictional litigation, a student finds alternative real-case strategies, and in-house/legal-ops users find business questions.
- A developer reaches actual source and installation guidance; a nonlawyer can distinguish examples from legal advice.
- At narrow viewport widths and in dark mode, navigation and calls to action remain legible without horizontal overflow.
- The built read-only image serves root and all existing demo artifacts; both deployed hosts serve the same homepage image.

**Verification:** Required repository checks and public runtime smoke pass; UAT findings and limitations are recorded; PR is merged and deployments are verified.

---

## Verification Contract

Run `make check` before every commit as AGENTS.md requires. Use the established external fixture locations and isolated pytest workers where available. Run frontend tests, lint, typecheck and build because shared navigation is affected. Inspect the homepage with the existing browser driver at desktop and mobile widths, keyboard-only navigation and system dark theme when supported. Check HTML destinations and the no-script contract. Run `tools/smoke_demo_runtime.py` against the built read-only Docker image and verify both deployed hosts, including the existing twenty-demo release. Record actual review coverage and any unavailable reviewer mechanism honestly.

## Definition of Done

All requirements and unit verification outcomes hold. No abandoned code or unrelated files enter the change. Persona UAT findings are fixed or explicitly documented with practical limitations. Required checks pass, the reviewed PR is merged, and development and production serve the verified image with rollback configuration retained.
