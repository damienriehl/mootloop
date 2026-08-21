# FOLIO Integration Route for MootLoop

Date: 2026-08-20

## Decision

Use `folio-enrich` directly as MootLoop's isolated, localhost-only document ingestion
and ontology-enrichment service. Treat the rendering code found at
`folio-api/folio_api/rendering` as an optional presentation adapter, not as an
alternative ingest engine. Keep MootLoop's converter boundary explicit so rendering,
normalization, and ontology enrichment cannot silently acquire one another's network
or data permissions.

This records D-05 B and D-12 C. It does not authorize a real hosted-matter read; D-03 A
still requires a fresh authorization after the synthetic deployed gate.

## Source findings

- Canonical ontology: `https://github.com/alea-institute/FOLIO/blob/main/FOLIO.owl`.
  The repository describes the ontology data as CC BY 4.0 and source code as MIT.
- `folio-enrich` accepts PDF, DOCX, HTML, Markdown, RTF, email, and plain text; performs
  ontology mapping and optional LLM/embedding stages; and exposes a job API.
- Its `owl_cache.py` and `owl_updater.py` already implement the useful reference
  pattern: conditional HEAD/ETag checks, XML validation, atomic cache replacement,
  one-version rollback, idle-before-reload, and embedding re-indexing.
- `folio-mapper` checks the canonical GitHub commits API on startup and periodically,
  falls back to raw-file HEAD/ETag, downloads through `folio-python`, hot-swaps only
  after a valid load, and rebuilds its index.
- The ALEA GitHub organization and local sibling repositories contain no separately
  named `folio-render` repository. The located rendering module is
  `folio-api/folio_api/rendering`; it formats class/property information and graph
  neighbors for presentation. If “folio-render” means another module, its exact
  package or repository location must be supplied before integrating it.

## MootLoop update contract

1. Check the canonical commits API shortly after startup and every 24 hours with
   jitter. The request contains only repository coordinates; it never contains matter
   data, IDs, filenames, or prompt content.
2. Compare the newest commit touching `FOLIO.owl` with the last validated commit. Fall
   back to a conditional HEAD request on the canonical raw URL when the commits API is
   unavailable or rate-limited.
3. Download a changed ontology into a candidate file. Enforce HTTPS host allowlists,
   response-size limits, timeouts, XML/RDF parsing, expected ontology identity and
   license metadata, content SHA-256, and class/property-count sanity checks.
4. Publish the candidate atomically only after validation. Retain the last known-good
   version and one rollback version plus a manifest containing canonical URL, commit,
   ETag, content hash, size, validation time, and parser/library versions.
5. Never mutate an active run's ontology. `RunContextManifest` pins the exact ontology
   content hash and source commit; a newly validated ontology becomes eligible only
   for a new run, consistent with D-15 A.
6. A failed periodic check leaves the last known-good ontology usable and surfaces a
   health warning. First boot without a validated local ontology fails closed only for
   FOLIO-dependent features, not unrelated MootLoop work.

## Protected-data boundary

`folio-enrich` can invoke configured LLM and embedding providers. For protected matter
content, MootLoop must not call an unconstrained default deployment. The service runs
inside the per-matter isolation boundary, listens only on loopback or a private
service network, receives fixed-schema requests, and has no general egress. Any
provider-backed stage must traverse MootLoop's destination allowlist and outbound
canary/redaction gate under an authorized provider policy. Purely local parsing,
spaCy/regex, and local embeddings are preferred for the initial synthetic gate.

## Route comparison

| Component | Best use in MootLoop | Why it is not the other route |
|---|---|---|
| `folio-enrich` | File conversion, text extraction, concept/entity/property enrichment, reviewable job results | It is a stateful ingestion/enrichment service with optional model calls and therefore needs isolation, job recovery, and egress controls. |
| `folio_api.rendering` | Human-readable ontology concept/property detail and graph presentation | It formats already-resolved ontology objects; it does not ingest matter documents or replace normalization/enrichment. |
| `folio-python` | In-process pinned ontology loading/search and deterministic FE-3 catalog access | It is the narrow deterministic dependency underneath both lanes and is preferable when no document enrichment is required. |

## Implementation consequence

- U-04B uses the synchronous extraction-only `POST /enrich/extract` route, not the
  stateful enrichment job API. The adapter is fixed-endpoint, bounded, and
  receipt-backed; its container has no egress or vault mount. Rendering remains a
  presentation-only follow-up rather than a competing conversion route.
- U-12 uses pinned `folio-python` access for deterministic catalog/search, backed by
  the update contract above.
- U-13/UI work may adapt rendering helpers only after confirming their license and
  removing assumptions tied to the standalone FOLIO API application.
- No startup check may auto-rebind an existing run, and no ontology update may be
  accepted merely because the remote file changed.
