# Deploying the MootLoop demo library

The public tier serves only reviewed, immutable projections: twenty prepared demos
and the original fictional discovery viewer. It has no active vault, uploads,
provider execution, credentials or private-client records. Curated public-record
summaries are permitted; private matter data remains outside this tier.

The protected matter application is separate. See `docs/deploy-matter.md` and
`docs/security-frontend.md`; do not change its containers for a demo-library rollout.

## Build from the pinned external artifact

Download the `release.tar` identified by `config/demos/release.json` into an external
folder. Never stage real-case artifacts inside the checkout, even in ignored paths.
The Docker build requires the named context and verifies the archive's committed
SHA-256 before validating and staging the complete release. Missing, mismatched or
incomplete content fails the build; no source acquisition occurs at runtime.

```bash
docker build --build-context demo_release=/external/demo-release \
  -t mootloop-demo:RELEASE_CODE_SHA .
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -p 127.0.0.1:8000:8000 mootloop-demo:RELEASE_CODE_SHA
```

The context must contain `release.tar`. The final image contains an explicit
allowlist of public-reader modules, required schema/package files, static assets,
locked dependencies and reviewed projections. It excludes the CLI, vault,
provider, publisher and preparation modules. It runs as UID 10001. Public assets
are readable by that user and not writable; the container can run read-only.

`MOOTLOOP_PUBLIC_ROOT=/app/public` is baked into the image. The old
`MOOTLOOP_DEMO_VAULT` variable is ignored; there is no vault fallback. The server
listens on `${PORT:-8000}`.

## Verify the built image

- `/health` is process liveness. `/ready` validates the complete active release and
  its hashes, including the original demo projection; require `demos: 20` and the
  expected release ID.
- `/` redirects to `/demos/`. Verify catalog, direct demo URLs, strategy selection,
  source links and revision-bound input downloads. Verify `/legacy` and its original
  eighteen discovery requests.
- Compare each download's SHA-256 with its catalog entry. Preserve source-support
  and unresolved-gate disclosures; a prepared run is not attorney approval.
- Run a smoke container with `--network none` and make loopback HTTP checks through
  `docker exec`. Verify writer modules and provider credentials are absent. This
  proves the image does not require outbound access.
- Keep the release archive SHA-256 and built image identifier with the deployment
  receipt. Promote the same image bytes from development to production.

## Existing deployment and rollback

The current public apps run on `hetzner-dev`, on the `coolify` Docker network:

| Environment | Application ID | Public URL |
| --- | --- | --- |
| Development | `dxr2q6xt90kwo2x37ubhc3x5` | https://mootloop.dev.openlegalstandard.org/demos/ |
| Production | `wx0ow6y0tfwnlxb5tupxg2dl` | https://mootloop.org/demos/ |

Use serial builds and inspect disk space before transferring an image. Retain the
previous image and deployment configuration; do not prune rollback images. The
persisted Compose files are under `/data/coolify/applications/<application-id>/`.
Do not print application environment values or API tokens while inspecting them.

At preparation time, the existing Coolify API token returned 401. The authorized
operator can use the existing SSH/Compose path, with a project-specific override
that changes only the demo service's image, public-root setting, read-only runtime
settings and readiness health check. Preserve its routing labels and network.
The health check must use Python against `/ready`; this minimal image does not
include curl or wget. Keep the override and previous image reference durably on the
server. A Coolify source build also needs the external named build context; do not
trigger an unconfigured source build and assume it will recreate the release.

Deploy development first. Verify `/ready`, all twenty snapshots/downloads, original
API responses and representative browser flows. Then promote the exact same image
to production under the release operator's authorization. The current owner has
authorized this rollout; future production deployments require their own authority.

For rollback, reapply the saved previous image/configuration to the same service,
recreate it without building, and verify its expected routes and health. Roll back
if readiness fails, content hashes differ, expected examples disappear, source
text executes, or private/runtime state becomes reachable. A disclosed failed legal
review gate is expected demo content and is not itself a deployment failure.

Monitor readiness, HTTP 5xx responses and browser console errors during the first
15 minutes after promotion. The release operator owns that check. Logs must not
contain private matters or credentials. Restore the saved deployment if those
checks show unintended harm; record the actual image and artifact digests.
