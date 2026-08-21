# Deploying the MootLoop matter tier

The matter tier is the **write** tier: real client vaults on a shared Coolify box,
Cloudflare Access-gated, driven by headless Claude. It is governed by this runbook plus
`docs/security-frontend.md` (the FE-0 threat model + pen-gate). The demo tier
(`docs/deploy.md`) is unrelated and stays read-only.

> **HARD RULE — no matter data before the pen-gate passes.**
> No real vault is loaded onto the box until every assertion in the FE-0 penetration
> checklist (`docs/security-frontend.md`) passes on the deployed stack.

## Topology (FD-5)

The core has two services, plus one isolated worker project per active matter
(`docker-compose.matter.yaml`):

- `web` — the Next.js BFF; the **only** publicly routable service (Cloudflare Access at the edge, JWT mirror in `middleware.ts`). It proxies to `api` and holds no vault access.
- `api` — the write-tier FastAPI over the live vaults; **internal only**, no published ports, reachable on the compose network as `http://api:8000`.
- `driver` — one per-matter engine worker; **internal only**, no ports, no web surface.
  It mounts one matter plus queue control metadata and is available only under the
  `matter-worker` compose profile.
- `egress-proxy` — one per worker project. The driver can reach only this internal
  proxy; authenticated Squid ACLs allow model traffic to `api.anthropic.com:443` and
  deny every other destination.

Only `web` crosses the public boundary. `api`, `driver`, and `egress-proxy` are never
published and never get an fqdn.

The isolation rationale and hostile-spike record are in
`docs/decisions/2026-08-21-hosted-matter-isolation-adr.md`.

## Images

- `web` — `frontend/Dockerfile` (Next.js standalone, non-root UID 1001).
- `api` — `Dockerfile.matter-api` (FastAPI, `--factory mootloop.web.api:create_matter_api`, pandoc, non-root UID/GID 3200, no vault baked).
- `driver` / `egress-proxy` — `Dockerfile.driver` (base deps + Node 22 + pinned
  `@anthropic-ai/claude-code`, Squid, pandoc, non-root UID/GID 3200).

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
- `api` and `driver` get **no** fqdn — leaving them internal is the intended state, not an oversight.

## Required Coolify env vars (names only — NEVER values)

Set in Coolify, never in the repo:

- `MOOTLOOP_INTERNAL_SECRET` — driver/BFF internal secret (also present in the host secrets file so `api` can verify it).
- `CF_ACCESS_TEAM_DOMAIN`
- `CF_ACCESS_AUD`
- `CF_ACCESS_ALLOWED_EMAIL`
- `MOOTLOOP_WORKER_ID` — the driver's worker id (e.g. `driver-1`).
- `MOOTLOOP_MATTER_ID` and `MOOTLOOP_MATTER_SOURCE` are supplied only by the validated
  host launcher; do not configure them globally in Coolify.

Set inside the compose (not Coolify): `MOOTLOOP_API_URL=http://api:8000`, `MOOTLOOP_MATTERS_ROOT=/srv/mootloop-matters`.

The `MOOTLOOP_INTERNAL_SECRET` and the download signing key also resolve on `api`/`driver` through the read-only `~/.mootloop/secrets.env` mount (`mootloop.secrets`); the container never needs the OAuth token in its environment.

## Host prerequisites (on the box, hand-applied)

- Create the service user with a fixed id: `mootloop`, **UID 3200 / GID 3200** (must match the image users).
- `/srv/mootloop-matters` — owned `mootloop:mootloop`, mode **0700**. Every vault lives under it, outside every repo tree.
- `~mootloop/.mootloop/` — dir mode **0700**; `~mootloop/.mootloop/secrets.env` mode **0600**, owned `mootloop:mootloop`.
- Populate the secrets file (`KEY=VALUE` lines): `MOOTLOOP_INTERNAL_SECRET`, **`MOOTLOOP_DOWNLOAD_SIGNING_KEY`** and **`MOOTLOOP_BACKUP_KEY`** (pre-seed both on the host — the containers mount `~/.mootloop` read-only, so first-use auto-derivation would fail closed), and the subscription token via `claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`.
- Add `MOOTLOOP_EGRESS_PROXY_PASSWORD` to the same secrets file. The deterministic
  driver loads and registers it for exact-value blocking.
- Create `~mootloop/.mootloop/egress-proxy-password`, mode **0600**, containing only
  the exact same password value (plus an optional trailing newline). The host launcher
  verifies its type, permissions, content, and equality with the driver secret. Compose
  mounts only this dedicated file into Squid; the proxy never receives the full secrets
  directory.
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

Core deployment does not activate the `matter-worker` profile. For an already-created
matter, use the host launcher as the `mootloop` service user. It validates the ID, exact
registry vault, matter identity, vault location, compose file, proxy credential, and
private engine-state root before deriving the bind sources and project name:

```bash
uv run mootloop driver start-matter-worker 2025-10-16-riehl-fence \
  --matters-root /srv/mootloop-matters \
  --engine-config-root /srv/mootloop-engine-config \
  --proxy-password-file /home/mootloop/.mootloop/egress-proxy-password \
  --compose-file docker-compose.matter.yaml
```

Do not invoke the profile with a hand-authored `MOOTLOOP_MATTER_SOURCE`: the launcher is
the path-containment choke point. Creation must fail if the vault, compose file,
matching proxy secret, or private engine-state root is absent. Verify the driver has
only five mounts: its one vault, its private persistent engine-state directory,
`.queue`, the read-only global canary registry, and the read-only secrets directory;
verify its only network is `driver-egress`. The provider's Landlock allowlist exposes
only immutable runtime files and the per-run Claude config tree; the matter vault,
`.queue`, global canary registry, and complete secrets directory are inaccessible to
the persona subprocess. Normal turns receive all matter context through fenced prompt
DATA, not direct filesystem reads. The driver injects only the required credential and
stores Claude state below `/srv/mootloop-engine-config/<matter-id>` so `--resume`
survives container replacement. Treat that state as credential-bearing: mode `0700`,
never back it up, and delete it only when the matter's resumable runs are retired.

The driver waits for Squid's healthcheck before starting. Squid receives only the
dedicated Compose secret and joins the outbound network; the driver remains on the
internal `driver-egress` network.

To drain, send a normal compose stop and allow the configured 630 seconds. SIGTERM is
observed at the next durable turn boundary; the worker releases its claim for recovery.
Never use `kill -9` for routine maintenance.

```bash
docker compose -p mootloop-worker-2025-10-16-riehl-fence stop -t 630 driver
docker compose -p mootloop-worker-2025-10-16-riehl-fence down
```

`down` removes only containers/networks. It must never remove volumes or any path under
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
