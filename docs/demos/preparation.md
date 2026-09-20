# Prepare and publish a demo release

The release contract is twenty qualifying entries: five fictional litigation cases,
five public-record counterfactuals with two strategies each, and ten fictional
business questions. The homepage is a separate scope. The public service is a
read-only projection reader; preparation uses the ordinary local task engine.

## Inputs and source review

The approved identities and task bindings are in `config/demos/catalog.yaml`.
`config/demos/real-cases.yaml` records acquisition URLs, hashes and historical
constraints. `config/demos/authorities.yaml` records primary authorities used in the
fictional examples. These files contain metadata, not real-case source packets.

Keep real-case source PDFs, substantive summaries, authored recipes and operational
runs outside every Git checkout, including ignored folders. Review public access,
source dates, support for each material assertion and redistribution permission.
A publicly accessible party filing is not automatically licensed for wholesale
redistribution. The release uses links and original attributed summaries.

For each historical case:

1. Fix the represented side and historical cutoff, including the stated time zone.
2. Separate record facts, party allegations, disputed propositions, law and explicit
   hypothetical changes. A filing's existence does not establish its allegations.
3. Bind each material claim to source IDs and precise locators. Acquired primary
   authorities must support the legal propositions used.
4. Keep later outcome sources outside strategy inputs. Later opinions cannot supply
   missing earlier evidence or preservation.
5. Prepare two genuinely distinct approaches, including opposing critique and a
   substantive revision. Explain how an approach could affect the selected outcome,
   and what would prevent it from doing so. A negative assessment is valid.
6. Inspect the actual replayed stages and their gates. Do not add approvals or remove
   blockers merely to make a public example look successful.

The Musk packet is expressly bounded to the donor-trust issues; the actual
applicants' reply was not acquired. The Tesla alternative monetary route expressly
changes earlier preservation and evidence development. Neither limitation is
silently treated as satisfied. Source support review is agent editorial review,
not attorney approval or proof of counterfactual causation.

## Prepare a candidate

`DemoPreparation` describes authored unit outputs, strategies, source support and
separate historical context. A fixture directory contains only `preparation.json`,
`matter.json` and the permitted document/discovery inputs. Fictional recipes live
under `fixtures/demos/{synthetic,business}`. Real-case recipes stay external.

```bash
uv run mootloop web prepare /external/fixture /external/preparation-work \
  /external/release/example-id/r1 --revision r1 --software-revision COMMIT_SHA
```

Use new work/output directories. The preparer drives the authored outputs through
the actual planner, records them, constructs a hash-bound replay, then runs that
replay in a fresh operational identity. It extracts the displayed stages from
recorded turns. It writes `snapshot.json` and `inputs.json`; it does **not** create
an editorial review receipt or attorney approval.

Inspect all stages and paired outputs, eligible source summaries, assumptions,
procedural constraints, actual gates and local instructions. A claim without
support must be removed, qualified or supported before the example qualifies.
Structural tests alone do not establish legal correctness.

## Bind review and publish atomically

After editorial inspection, create a `PublicationReview` as `review.json` beside
the exact snapshot and input bundle. Record the real reviewer kind, review scope,
limitations and exact SHA-256 digests. `publication_eligible` means suitable for this
educational publication; `legal_approval` remains false. Do not mechanically mark
an uninspected example reviewed.

Each catalog entry binds the descriptor, revision and all three file digests.
`catalog.json` also binds `legacy.json`, the offline projection of the original
fictional discovery demonstration. Generate that projection with
`mootloop.web.legacy_prepare.project_legacy` from `build_demo_vault` in an isolated
external workspace. The legacy file contains enumerated API responses and permitted
Markdown/JSON deliverables, not the vault itself.

```bash
uv run python tools/validate_demo_release.py /external/release
uv run python tools/check_demo_replays.py /external/release /external/new-replay-check
uv run mootloop web publish /external/release /external/public
```

The publisher validates the exact twenty-entry manifest, hashes, source contracts,
input allowlist, review bindings and credential/canary markers before atomically
switching `CURRENT`. A failed entry leaves the previous release active. Changed
content requires a new demo revision and a new review. Do not edit published bytes
in place. Withdrawing content requires a new complete reviewed release; missing
or unavailable revisions return explicit errors, never private-state fallback.

## Archive and pin

Create an uncompressed, deterministic USTAR `release.tar` outside the checkout.
Include only `catalog.json`, `legacy.json` and each demo revision's `snapshot.json`,
`inputs.json` and `review.json`: 62 regular files. Use sorted names, zero timestamps
and fixed ownership. Do not include original PDFs, preparation vaults, secrets,
canary registries or arbitrary extra files.

Publish the permitted archive as an immutable GitHub release asset. Commit only its
release ID, HTTPS URL and SHA-256 in `config/demos/release.json`. Never replace an
asset under the same release identity. Artifact hashes refer to the exact files in
this archive; the typed detail API may serialize JSON with different whitespace.

```bash
uv run python tools/validate_demo_release.py /external/release.tar \
  --pin config/demos/release.json --stage /external/staged-public
```

Extraction verifies captured archive bytes against the pin and rejects traversal,
links, devices, duplicate names, excess entries and oversized payloads before
publication. Ordinary tests use fictional fixtures; external real-case preparation
tests run only when `MOOTLOOP_REAL_DEMO_FIXTURES` points to a curated external packet.
CI downloads the pinned public release artifact, not mutable court source material,
and exercises all prepared strategies without paid providers.
