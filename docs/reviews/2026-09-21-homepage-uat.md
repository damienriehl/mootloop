# Homepage acceptance review — 2026-09-21

The homepage explains MootLoop as an open-source legal AI workflow, identifies the simulated personas and human attorney responsibilities, and offers direct paths into the existing demo collections and local installation.

## Coverage

These are agent-executed persona walkthroughs, not research with human participants. Browser testing used the existing browser connection against the local public runtime. The browser's system dark theme was active. Desktop and mobile screenshots were inspected; light-theme styling was checked against the existing shared tokens, not through a separate light-mode browser session. Screen-reader and cross-browser testing were not performed.

| Persona | Journey and observed result |
| --- | --- |
| Lawyer | Homepage → fictional litigation → Employment retaliation. Five fictional matters appeared; the complaint and review stages loaded. |
| Law student | Homepage → historical counterfactuals → Musk v. Altman / OpenAI. Five cases appeared; switching to the donor-commitment strategy changed the selected strategy heading. Historical cutoffs and hypothetical limits remained visible. |
| In-house counsel | Homepage → Ask in-house counsel. All ten business questions appeared, including supplier termination, customer data and liability negotiation. |
| Legal operations | Business collection → supplier termination → Review gates. The section anchor worked and unresolved review requirements remained available. |
| Developer | Homepage → local-use section → installation guide on GitHub. The guide loaded with installation and replay commands; source links identify the repository, Python core and application UI accurately. |
| Interested nonlawyer | Homepage explains simulated review and labels the illustration. Demo disclosures distinguish prepared scripts from live runs, attorney-approved advice and predictions. |

## Browser and HTTP checks

| Route or behavior | Result |
| --- | --- |
| `/` | Pass: readable value proposition, workflow, features, audience guidance, demo links and local installation. |
| `/#local` | Pass: installation guidance and source links reached on mobile. |
| `/demos/?collection=synthetic` | Pass: 5 of 20 demos. |
| `/demos/?collection=public-record` | Pass: 5 of 20 demos. |
| `/demos/?collection=business` | Pass: 10 of 20 demos. |
| `/demos/employment-retaliation?revision=r1` | Pass: reader loads from the collection. |
| `/demos/musk-openai?revision=r1` | Pass: reader and both strategy choices work. |
| `/demos/supplier-termination?revision=r1` | Pass: business reader and review-gate navigation work. |
| Library wordmark | Pass: returns to the homepage. |
| Keyboard | Pass: skip link focuses the main region; Tab focuses Explore the demos with a visible outline; Enter opens the full twenty-demo catalog. |
| Mobile | Pass at 390 × 844: 375px document/client widths, no horizontal overflow, usable navigation and local-use section. |
| Browser errors | None observed during the homepage and collection journeys. |
| No-script contract | Homepage HTML has no scripts or forms; its content and ordinary links are served directly. This was verified through HTTP and source inspection, not browser JavaScript disabling. |
| Missing release | Homepage returns 200 while catalog and readiness return 503; existing CSP and nosniff headers remain present. |
| Read-only image | Root HTML/CSS, 20 demo snapshots and input hashes, 25 strategy trails, and 18 legacy requests pass. Runtime UID remains 10001; writer modules and provider credentials are absent. |

## Findings resolved

- Clarified the opening copy to say the personas are simulated rather than leaving visitors to infer that judicial review was simulated.
- Updated the existing API test that expected the old root redirect. The first full run had 1 failure and 1,440 passes; the failure was that obsolete expectation.
- Removed a duplicate reduced-motion rule from the new stylesheet. The existing shared rule still disables smooth scrolling when reduced motion is requested.

## Review limits

Five plan-review lenses completed without findings. Native review handles were reused after new-agent creation hit the session's thread limit; no cross-lens independence promotion was claimed. The additional external-model review was not run because the previously rejected egress permission remains in force. Code review and release verification are recorded with the pull request and deployment receipt.
