# Demo-library persona acceptance review

Date: September 20, 2026. Scope: the 20-demo library released in PR #69,
plus the navigation and reading fixes accompanying this report. The homepage
is excluded and has not been started.

## Method and result

**PASS for the simulated persona tasks below after correction.** One agent
performed these walkthroughs using the connected browser. These are not interviews,
independent reviews, or acceptance sign-offs from actual lawyers or other users.
Legal accuracy and citation currency were not independently re-certified by this pass.

The initial walkthrough used `https://mootloop.org`. Corrections were retested on
the local public viewer with the same pinned release, `2026-09-20-r1`. Browser
testing used visible controls and rendered content, desktop and a 390 × 844
viewport, keyboard activation, and console inspection. The viewport override
was reset afterward. Screenshots were inspected in the browser session; no
real-case text or screenshots were copied into this repository.

## Persona tasks

| Persona | Task and acceptance criterion | Observed result |
| --- | --- | --- |
| Practicing litigator | Find a federal trial example; inspect its original draft, critique, revision, assessment, and limits. Section links must reveal the requested material. | Court filter returned Product liability and Musk/OpenAI. All four stages and unresolved gates were present. Section navigation failed initially; corrected and retested with both sections remaining expanded. |
| Law student | Read the civil-rights oral-argument preparation on a phone; distinguish questions, answers, concessions, and a prepared assessment from an actual hearing or predicted result. | Required sections and explicit written/scripted labels were present. At 390 × 844, document client/scroll widths were both 375 px, with 17.2 px prose and no horizontal overflow. Keyboard section activation opened and focused the draft after correction. |
| In-house counsel | Search for customer-data questions, identify a recommendation, decision conditions, responsible teams, and a usable draft action document. | Search returned the AI feature and data-incident examples. The AI example supplied recommendation, risks, options, named next-step owners, and a checklist/rider, while retaining unresolved review gates. Raw bold markers in draft labels were corrected and retested. |
| Legal operations | Distinguish a finished run from approval; inspect recorded gate results and claim/source provenance; determine whether the hosted viewer collects documents or keys. | Expanded recorded gates distinguish pass, fail, and not evaluated. The example explicitly says it is not ready for clean export. Claim-support disclosure opens. Public read-only, no-live-call, and no-attorney-approval disclosures remain visible; no upload or key-entry flow exists. |
| Developer | Follow installation and replay instructions from a fresh clone and downloaded public bundle, with an external vault and no model calls. | `uv sync`, CLI help, import, replay-script extraction, run start, drive, and status all returned exit code 0. The supplier-advice replay finished 13 turns with zero tokens and zero spend, retaining its open decision. |
| Interested nonlawyer / business user | Find a recognizable case or business question; distinguish a fictional example, a hypothetical alternative, and the historical outcome; recover from no matches or a broken link. | All historical cases expose two strategy choices, comparison, cutoff, and a separately labeled later outcome. No-match search gives an explanation and Clear filters restores results. An unknown demo shows an unavailable message with a working return link. Comprehension by actual nonlawyers remains untested. |

## Route coverage

All 20 detail routes below were loaded in the browser after their expected heading
became visible. The five historical pages were tested with **both** strategy
selections, giving 25 trails in total. All fictional litigation pages expose four
stages, gates, local inputs, and authorship limits. All ten business pages also
expose next steps and a secondary deliverable.

| Route | Result | Notes |
| --- | --- | --- |
| `/` | Pass | Redirects to the library; retested locally. |
| `/demos/` | Pass | 20 entries; court and text filters; empty-state recovery; keyboard controls. |
| `/demos/civil-rights-argument?revision=r1` | Pass | Student, mobile, keyboard, and section-link checks. |
| `/demos/employment-retaliation?revision=r1` | Pass | Complaint stages and boundaries. |
| `/demos/land-use-appeal?revision=r1` | Pass | State appellate example. |
| `/demos/product-liability?revision=r1` | Pass after fix | Federal trial example; anchor and deep-link regression. |
| `/demos/supplier-discovery?revision=r1` | Pass | Discovery example. |
| `/demos/dominion-fox?revision=r1` | Pass | Both strategies, comparison, separate outcome. |
| `/demos/epic-apple?revision=r1` | Pass | Both strategies, comparison, separate outcome. |
| `/demos/google-oracle?revision=r1` | Pass | Both strategies, comparison, separate outcome. |
| `/demos/musk-openai?revision=r1` | Pass | Both strategies, comparison, separate outcome. |
| `/demos/tesla-tornetta?revision=r1` | Pass | Both strategies; earlier-preservation hypothesis is explicit. |
| `/demos/acquisition-diligence?revision=r1` | Pass | Business decision and action document. |
| `/demos/ai-customer-data?revision=r1` | Pass after fix | Counsel and legal-operations tasks; readable bold labels. |
| `/demos/comparative-advertising?revision=r1` | Pass | Business decision and action document. |
| `/demos/competitor-recruitment?revision=r1` | Pass | Business decision and action document. |
| `/demos/customer-data-incident?revision=r1` | Pass | Business decision and action document. |
| `/demos/distributor-restrictions?revision=r1` | Pass | Business decision and action document. |
| `/demos/liability-cap?revision=r1` | Pass | Business decision and action document. |
| `/demos/open-source-release?revision=r1` | Pass | Business decision and action document. |
| `/demos/supplier-termination?revision=r1` | Pass | Fresh-clone local replay also completed. |
| `/demos/worker-classification?revision=r1` | Pass | Business decision and action document. |
| `/legacy` | Pass | 18 requests, 219 turns; Deliverables tab opens; synthetic disclosure. |
| `/demos/not-a-demo` | Pass | Expected error state; return navigation succeeds. |

## Findings and corrections

1. **Section links left work product collapsed and hash navigation reset state.**
   Reproduction: open Product liability, select Initial draft in the page navigation.
   The browser scrolled to a closed disclosure. Hash-only history navigation could
   also reload the snapshot and discard expanded disclosures. The fix reveals the
   target and avoids refetching the same page for an anchor change. Deep links and
   strategy changes still render their selected content. Regression tests cover
   repeated anchors, preserved expansion, history navigation, and deep links.
2. **Keyboard focus needed to follow navigation.** During correction, keyboard
   testing caught a focus regression. Section navigation now focuses its summary;
   Skip to content focuses the main region. Both were retested in the browser.
3. **Draft labels displayed literal bold markers.** The prose renderer now creates
   `strong` elements for paired bold markers using text nodes. Source markup remains
   inert; the regression test includes malicious markup inside bold delimiters and
   rejects executable elements and unsafe source links.

No unexpected browser console errors were observed in the completed walkthroughs.
Regression evidence: 45 frontend tests passed, including the expanded navigation
and unsafe-markup regression; frontend lint and type checks passed. Repository
`make check` passed with 1,440 tests, strict mypy, lint, and 91% coverage.
Deployment evidence is recorded separately with the release.

## Not covered by this pass

- **Actual participant UAT: not performed.** No lawyer, student, developer, legal-ops
  practitioner, or lay participant was recruited or asked to sign off.
- **Screen-reader and cross-browser certification: not performed.** Keyboard,
  semantic browser state, and one responsive viewport were checked; this is not a
  complete accessibility audit or a Safari/Firefox test matrix.
- **Windows/macOS installation: not performed.** Fresh installation and replay
  were exercised on Linux with the documented Python and uv prerequisites available.
- **Live model execution, hosted uploads, BYOK, and the new homepage: excluded.**
  They are not capabilities of the public demo library under the approved scope.

These limits remain explicit; the simulated passes do not substitute for user
research or professional legal review.
