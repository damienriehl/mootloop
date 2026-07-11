# Third-Party Components

Every external component MootLoop uses (or plans to use) is logged here with its
license and integration mode. **Mode** is one of:

- `dependency-planned` — will be pulled in as a package dependency.
- `copy-planned` — source will be copied/adapted into this repo (attribution + license
  retained per the component's terms).
- `dependency` / `copy` — currently integrated.

| Component | Source | License | Mode |
|-----------|--------|---------|------|
| folio-python | https://github.com/alea-institute/folio-python | MIT | dependency-planned |
| alea-intake components (convergence, scoring, DOCX export) | https://github.com/alea-institute (alea-intake) | MIT | copy-planned (Phase 3 / 7) |
| FreeLawProject eyecite | https://github.com/freelawproject/eyecite | BSD-2-Clause | dependency-planned (Phase 4) |

Update this table in every ship that adds, removes, or changes a third-party
component, and comply with each component's license (retain MIT/BSD/CC-BY notices
and attribution).
