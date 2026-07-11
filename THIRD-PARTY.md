# Third-Party Components

Every external component MootLoop uses (or plans to use) is logged here with its
license and integration mode. **Mode** is one of:

- `dependency-planned` — will be pulled in as a package dependency.
- `copy-planned` — source will be copied/adapted into this repo (attribution + license
  retained per the component's terms).
- `dependency` / `copy` — currently integrated.

| Component | Source | License | Mode |
|-----------|--------|---------|------|
| python-docx | https://github.com/python-openxml/python-docx | MIT | dependency (Phase 1 — `.docx` normalization) |
| folio-python | https://github.com/alea-institute/folio-python | MIT | dependency-planned |
| alea-intake `ConvergenceEvaluator` structure | alea-intake `backend/app/services/analysis/convergence.py` @ `18d8cf5` (+ `ConvergenceSignals`/`ConvergenceWeights` in `schemas.py`) | MIT | copy (Phase 3 — **structure only**, re-mapped for drafting) |
| alea-intake components (scoring, DOCX export) | https://github.com/alea-institute (alea-intake) | MIT | copy-planned (Phase 7) |
| FreeLawProject eyecite | https://github.com/freelawproject/eyecite | BSD-2-Clause | dependency (Phase 4 — local citation extraction) |
| httpx | https://github.com/encode/httpx | BSD-3-Clause | dependency (Phase 4 — the sole HTTP-client layer) |
| respx | https://github.com/lundberg/respx | BSD-3-Clause | dependency (dev — mocks httpx in citation tests; no live network) |
| CourtListener v4 API | https://www.courtlistener.com/help/api/rest/ | data via free token | external service (Phase 4 — citation-lookup verification) |
| MN Revisor (statutes / court rules) | https://www.revisor.mn.gov/ | public stable-URL pages | external service (Phase 4 — no API; stable-URL scrape) |

### Copy note — `ConvergenceEvaluator` (Phase 3)

`src/mootloop/convergence.py` copies the **weighted-signal structure** of alea-intake's
`ConvergenceEvaluator` (pinned commit `18d8cf5`) — the evaluator/signals/weights
shape — under its MIT license. Per plan D1, the signals are **re-mapped for drafting**:
alea's intake signals (`user_fatigue`, intake `coverage`, `diminishing_gaps`,
`confidence_plateau`) do **not** carry over. `user_fatigue` is dropped; the loop rule
is an explicit AND of three floors (score-delta / material-change / coverage) rather
than a weighted-threshold vote. No alea-intake code is imported at runtime.

Update this table in every ship that adds, removes, or changes a third-party
component, and comply with each component's license (retain MIT/BSD/CC-BY notices
and attribution).
