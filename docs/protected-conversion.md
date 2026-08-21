# Protected corpus conversion

MootLoop converts unsupported, text-bearing documents through `folio-enrich`'s
extraction-only endpoint. The converter is a separate, digest-pinned container on a
private per-matter network. It receives document bytes over fixed-schema JSON, mounts
no vault or secrets, has a read-only root filesystem and bounded scratch space, and
has no outbound network.

This lane handles `unsupported_format` actions only. OCR-required, password-protected,
corrupt, unreadable, and oversized documents stay in the manual action queue. Role and
privilege review remain separate human decisions. A successful conversion is not
run-visible until those reviews are also complete.

## Reviewed implementation

- Source repository: `https://github.com/alea-institute/folio-enrich`
- Reviewed source commit: `f5364365346d93a3aa01fd5fecf219090afe5410`
- API: synchronous `POST /enrich/extract`
- Supported conversion inputs: PDF, DOCX, RTF, HTML, EML, and MSG
- Rendering comparison: `folio-api/folio_api/rendering` formats ontology objects for
  presentation; it does not extract document text and is not an ingest alternative.

The host launcher rejects mutable image tags and every source commit other than the
reviewed commit. Before deployment, build or select an image with verifiable provenance
from that commit, record its exact `sha256` digest in the deployment evidence, and pass
the full digest reference to the launcher. A digest by itself does not prove its source,
so provenance verification is part of the deployment gate.

## Start an isolated matter worker

Run this only for a synthetic matter until the deployed synthetic gate passes and a
fresh real-matter authorization is recorded:

```bash
uv run mootloop driver start-matter-worker <synthetic-matter-id> \
  --matters-root /srv/mootloop-matters \
  --engine-config-root /srv/mootloop-engine-config \
  --proxy-password-file /home/mootloop/.mootloop/egress-proxy-password \
  --folio-enrich-image ghcr.io/alea-institute/folio-enrich@sha256:<64-hex-digest> \
  --folio-enrich-commit f5364365346d93a3aa01fd5fecf219090afe5410 \
  --compose-file docker-compose.matter.yaml
```

The launcher supplies the image/commit commitment to both the driver and Compose. The
driver reaches `http://folio-enrich:8731` only on `driver-conversion`; the converter
joins no proxy or outbound network. It has no host ports, bind mounts, Docker socket,
credentials, or model-provider route. Its upstream auto-update, embedding, and local
model-manager features are disabled; writable jobs and feedback live only in bounded
ephemeral `/tmp`. Ontology caches remain read-only, and the container has no route to
refresh them. Canonical FOLIO update checks belong to U-12's separate matter-free
updater, never this matter-data sidecar.

## Convert and review

Execute the conversion command inside the bound driver container so the fixed hosted
service name is reachable:

```bash
uv run mootloop corpus actions /srv/mootloop-worker/matter
uv run mootloop corpus convert /srv/mootloop-worker/matter <doc-id>
uv run mootloop corpus actions /srv/mootloop-worker/matter
```

The service reads the one content-addressed original through a no-follow descriptor,
checks its full hash against the document ID, bounds input/output/response sizes, and
refuses non-fixed endpoints or response metadata. It writes normalized text, then a
self-verifying receipt binding matter, document, input, output, converter image,
reviewed commit, actor, and timestamp, and only then atomically promotes the manifest.
After a crash between receipt publication and manifest promotion, retry validates the
exact receipt and output and promotes without calling the converter again. Tampered or
symlinked inputs, outputs, and receipts fail closed.

## Evidence and closure

For the synthetic deployment record:

1. Record the image provenance statement, source commit, and resolved image digest.
2. Confirm the converter has only `driver-conversion`, no mounts, no published ports,
   a read-only root, all capabilities dropped, and `no-new-privileges`.
3. Exercise successful PDF extraction plus oversized output, malformed response,
   symlink/traversal, endpoint, crash/retry, and receipt-tamper failures.
4. Confirm every failure leaves the manifest unpromoted and produces no matter-data
   egress.
5. Stop. Do not inspect a hosted real-matter folder until the synthetic deployed gate
   passes and the user provides the fresh D-03 authorization.

The authorized protected-folder review then records either successful conversion or a
specific manual disposition for every residual action. It must not silently drop a
file or treat an unreviewed document as run-visible.
