# Deploying the MootLoop matter tier

The matter tier is the **write** tier: real client vaults on a shared Coolify box,
Cloudflare Access-gated, driven by headless Claude. It is governed by this runbook plus
`docs/security-frontend.md` (the FE-0 threat model + pen-gate). The demo tier
(`docs/deploy.md`) is unrelated and stays read-only.

> **HARD RULE — no matter data before the pen-gate passes.**
> No real vault is loaded onto the box until every assertion in the FE-0 penetration
> checklist (`docs/security-frontend.md`) passes on the deployed stack.

## Topology (FD-5)

The core has two services in `docker-compose.matter.yaml`, plus one isolated worker
project per active matter from `docker-compose.worker.yaml`:

- `web` — the Next.js BFF; the **only** publicly routable service (Cloudflare Access at the edge, JWT mirror in `middleware.ts`). It proxies to `api` and holds no vault access.
- `api` — the write-tier FastAPI over the live vaults; **internal only**, no published ports, reachable on the compose network as `http://api:8000`.
- `driver` — one per-matter engine worker; **internal only**, no ports, no web surface.
  It mounts one matter plus queue control metadata and is available only under the
  `matter-worker` compose profile.
- `egress-proxy` — one per worker project. The driver can reach only this internal
  proxy; authenticated Squid ACLs allow model traffic to `api.anthropic.com:443` plus
  D-18-A public legal-source traffic to `www.courtlistener.com:443`,
  `api.courtlistener.com:443`, and `www.revisor.mn.gov:443`, and deny every other
  destination. Independent proxy identities prevent the model subprocess from using
  the legal-source destinations and prevent deterministic application traffic from
  using the model destination. Legal-source requests are additionally constrained to
  fixed methods and paths in `mootloop.citations.http` and pass the outbound privacy
  gate.
- `folio-enrich` — one per worker project. It extracts text on a separate private
  `driver-conversion` network, has no outbound network or vault mount, and is selected
  only by an exact image digest plus reviewed source commit.

Only `web` crosses the public boundary. `api`, `driver`, `egress-proxy`, and
`folio-enrich` are never published and never get an fqdn.

The isolation rationale and hostile-spike record are in
`docs/decisions/2026-08-21-hosted-matter-isolation-adr.md`.

## Images

- `web` — `frontend/Dockerfile` (Next.js standalone, non-root UID 1001).
- `api` — `Dockerfile.matter-api` (FastAPI, `--factory mootloop.web.api:create_matter_api`, pandoc, non-root UID/GID 3200, no vault baked).
- `driver` / `egress-proxy` — `Dockerfile.driver` (base deps + Node 22 + pinned
  `@anthropic-ai/claude-code`, Squid, pandoc, non-root UID/GID 3200).
- `folio-enrich` — external image pinned by its exact OCI SHA-256 digest. The host
  launcher accepts only the reviewed source commit documented in
  `docs/protected-conversion.md`; verify source-to-image provenance before deployment.

The non-root driver applies an unprivileged Landlock ruleset before every model process.
It requires no setuid helper, added capability, unconfined seccomp profile, or Docker
socket. Unsupported kernels fail closed before the model starts.

The UID/GID **3200** on `api`/`driver` must match the host `mootloop` user so the bind-mounted vaults and secrets file are owned correctly.

## Coolify app creation (API)

Create it as a **Docker Compose application from this git repo** (not a raw-compose service):

- Endpoint: the Coolify **applications** endpoint.
- `build_pack`: `dockercompose`.
- `docker_compose_location`: `docker-compose.matter.yaml` (path within the repo).
- `instant_deploy`: `false` — never auto-deploy; env vars must be set first, and the pen-gate must pass before real data.
- Repo: `https://github.com/damienriehl/mootloop`, branch `main`.

### The fqdn gotcha (per-service, WITH scheme)

Coolify's `SERVICE_FQDN_*` magic env vars are read only on first parse and editing them later does **not** regenerate Traefik labels (verified — see `websites/docs/solutions/coolify-compose-service-fqdn.md`). Set the domain on the **web sub-service's** application row, stored **with scheme** (`https://…`), which is what triggers the `tls.certresolver=letsencrypt` labels; then redeploy.

- `web` gets the fqdn (e.g. `https://mootloop-matter.dev.openlegalstandard.org`).
- `api` gets **no** fqdn — leaving it internal is the intended state, not an oversight.

## Required Coolify env vars (names only — NEVER values)

Set in Coolify, never in the repo:

- `MOOTLOOP_INTERNAL_SECRET` — BFF/API internal secret (also present in the host secrets
  file so `api` can verify it).
- `CF_ACCESS_TEAM_DOMAIN`
- `CF_ACCESS_AUD`
- `CF_ACCESS_ALLOWED_EMAIL`

Set inside the compose (not Coolify): `MOOTLOOP_API_URL=http://api:8000`, `MOOTLOOP_MATTERS_ROOT=/srv/mootloop-matters`.

The `MOOTLOOP_INTERNAL_SECRET` and download signing key also resolve on `api` through
the read-only `~/.mootloop/secrets.env` mount (`mootloop.secrets`). The API never needs
the OAuth token in its environment.

## Host prerequisites (on the box, hand-applied)

- Create the service user with a fixed id: `mootloop`, **UID 3200 / GID 3200** (must match the image users).
- `/srv/mootloop-matters` — owned `mootloop:mootloop`, mode **0700**. Every vault lives under it, outside every repo tree.
- `~mootloop/.mootloop/` — dir mode **0700**; `~mootloop/.mootloop/secrets.env` mode **0600**, owned `mootloop:mootloop`.
- Populate the secrets file (`KEY=VALUE` lines): `MOOTLOOP_INTERNAL_SECRET`, **`MOOTLOOP_DOWNLOAD_SIGNING_KEY`** and **`MOOTLOOP_BACKUP_KEY`** (pre-seed both on the host — the containers mount `~/.mootloop` read-only, so first-use auto-derivation would fail closed), and the subscription token via `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`.
- Add distinct `MOOTLOOP_EGRESS_PROXY_PASSWORD` and
  `MOOTLOOP_LEGAL_EGRESS_PROXY_PASSWORD` values to the same secrets file. The
  deterministic driver loads and registers both for exact-value blocking.
- Create `~mootloop/.mootloop/egress-proxy-password`, mode **0600**, containing only
  the exact model password value, and `~mootloop/.mootloop/legal-egress-proxy-password`,
  mode **0600**, containing only the exact legal-source password value (either may have
  one trailing newline). The host launcher verifies each file's type, permissions,
  content, equality with its driver secret, and inequality with the other password.
  Compose mounts only these dedicated files into Squid; the proxy never receives the
  full secrets directory.
- **`MOOTLOOP_BACKUP_KEY`** is the AES-256 key that encrypts vault backups at rest (see `docs/backup.md`). Escrow it separately and **never** back it up alongside the archives it seals.
- Run `claude setup-token` as the `mootloop` user so the token lands in that user's secrets file (the crown-jewel asset; never printed, never committed).

## Per-hostname AOP (Traefik `tls.options`) — hand-applied on the box

Authenticated Origin Pulls are enforced **per hostname** on the `web` vhost only (NOT global AOP — the box's other public apps must stay open). This is a Traefik dynamic-config file applied by hand on the box; Coolify does not generate it.

```yaml
# Placeholder — dynamic-config snippet, hand-applied on the box (NOT generated by Coolify).
# tls:
#   options:
#     mootloop-matter-aop:
#       clientAuth:
#         caFiles:
#           - /path/to/cloudflare-aop-client-ca.pem
#         clientAuthType: RequireAndVerifyClientCert
# Attach `mootloop-matter-aop` as the router's tls.options for the web vhost ONLY.
```

Fill the CA path and attach the option to the web router's vhost; leave every other vhost untouched.

## FE-0 pen-gate

Before any real matter data is loaded, the FE-0 penetration checklist in
`docs/security-frontend.md` must pass on the **deployed** stack (Access JWT + AOP +
internal-secret + rate-limit + audit assertions). No vault touches the box until it does.

## Create / drain / remove / recover workers

The Coolify core application contains no worker services. For an already-created
matter, use the host launcher as the `mootloop` service user. It validates the ID, exact
registry vault, matter identity, vault location, compose file, proxy credential, and
private engine-state root before deriving the bind sources and project name and
activating the `matter-worker` profile in `docker-compose.worker.yaml`. Set
`MOOTLOOP_WORKER_ID` in the launcher environment when a stable non-default worker ID is
required; otherwise the driver image uses `driver-1`. The launcher alone supplies
`MOOTLOOP_MATTER_ID`, `MOOTLOOP_MATTER_SOURCE`, and the engine-state source.

```bash
uv run mootloop driver start-matter-worker 2025-10-16-riehl-fence \
  --matters-root /srv/mootloop-matters \
  --engine-config-root /srv/mootloop-engine-config \
  --proxy-password-file /home/mootloop/.mootloop/egress-proxy-password \
  --legal-proxy-password-file /home/mootloop/.mootloop/legal-egress-proxy-password \
  --folio-enrich-image ghcr.io/alea-institute/folio-enrich@sha256:<64-hex-digest> \
  --folio-enrich-commit f5364365346d93a3aa01fd5fecf219090afe5410 \
  --compose-file docker-compose.worker.yaml
```

Do not invoke the profile with a hand-authored `MOOTLOOP_MATTER_SOURCE`: the launcher is
the path-containment choke point. Creation must fail if the vault, compose file,
matching proxy secret, or private engine-state root is absent. Verify the driver has
only five mounts: its one vault, its private persistent engine-state directory,
`.queue`, the read-only global canary registry, and the read-only secrets directory;
verify its only outbound-capable route is `driver-egress`; `driver-conversion` reaches
only the no-egress converter. The provider's Landlock allowlist exposes
only immutable runtime files and the per-run Claude config tree; the matter vault,
`.queue`, global canary registry, and complete secrets directory are inaccessible to
the persona subprocess. Normal turns receive all matter context through fenced prompt
DATA, not direct filesystem reads. The driver injects only the required credential and
stores Claude state below `/srv/mootloop-engine-config/<matter-id>` so `--resume`
survives container replacement. Treat that state as credential-bearing: mode `0700`,
never back it up, and delete it only when the matter's resumable runs are retired.

The driver waits for both Squid and folio-enrich healthchecks before starting. Squid
receives only the dedicated Compose secret and joins the outbound network. The driver
joins the internal `driver-egress` and `driver-conversion` networks; folio-enrich joins
only `driver-conversion`. See `docs/protected-conversion.md` for conversion evidence and
the real-folder authorization gate.

To drain, send a normal compose stop and allow the configured 630 seconds. SIGTERM is
observed at the next durable turn boundary; the worker releases its claim for recovery.
Never use `kill -9` for routine maintenance.

```bash
uv run mootloop driver stop-matter-worker 2025-10-16-riehl-fence
uv run mootloop driver remove-matter-worker 2025-10-16-riehl-fence
```

Both lifecycle commands derive the same collision-resistant project name and worker
Compose file used at startup. They intentionally do not require the vault, engine
state, proxy credentials, or converter image to remain available, so emergency drain
cannot be blocked by a damaged startup asset. Teardown interpolation uses fixed,
non-secret placeholders and never runs `up`. Removal runs `down` without `--volumes`;
it removes only containers/networks and must never remove any path under
`/srv/mootloop-matters`. Recovery recreates the same project and matter binding. Queue
visibility leases plus stale-worker heartbeat reclaim make the last unjournaled turn
eligible again; completed journaled turns are not repeated.

## Deploy / drain contract

- `web` and `api` — **redeploy anytime**. They hold no in-flight turn state; a rolling restart is safe.
- `driver` — **drain-required**, using the per-matter lifecycle above.
- Prod deploys are **ask-gated** (house rule) — confirm before deploying the matter tier.

A local built-image Landlock mask probe and Compose validation were performed for this
change. Deployed network-namespace and proxy proof remains U-17A work and requires
fresh D-03 deployment authorization.

## Backup / restore

Vault backups are idle-only, lock-consistent, **AES-256-GCM encrypted at rest**, and pushed
off-box. The full posture — key pre-seeding, off-box `rclone`/`rsync` push, the stated RPO,
and the restore procedure + drill — lives in `docs/backup.md`.
