# Deploying the MootLoop demo server

> **Deploying the matter (write) tier instead?** See `docs/deploy-matter.md` +
> `docs/security-frontend.md` — the matter tier is governed by those, not this doc. The
> HARD RULE below applies to **this demo tier**.

The public demo is a **read-only** server over a synthetic vault **baked at image
build time**. It has zero matter-data mechanisms: no uploads, no `~/Matters`
access, no secrets, no LLM calls at runtime.

> **HARD RULE — no matter data on servers.**
> DEV and PROD host the pre-baked synthetic demo only. Real matters run
> **locally** via Claude Code (see `docs/quickstart-live-matter.md`); their
> vaults never leave the attorney's machine.

## Image

- `Dockerfile` at the repo root: `python:3.12-slim`, pandoc installed (DOCX
  render at bake time), uv with the `web` extra, non-root user.
- `RUN mootloop web bake /app/demo-vault` bakes the demo run at **build** time
  — deterministic, offline, `FakeLLMProvider`.
- Serves on `${PORT:-8000}`; `/health` returns `{"status": "ok", ...}`;
  baked `HEALTHCHECK` curls it.

Local smoke test:

```bash
docker build -t mootloop-demo .
docker run --rm -p 8000:8000 mootloop-demo
curl http://localhost:8000/health
```

## Coolify (house recipe)

One Coolify project **`mootloop`** on the dev box, one app per environment,
each built from this repo's Dockerfile (dockerfile buildpack), port **8000**.

Operational notes (verified on the box):

- **Serial builds:** `concurrent_builds=1` — concurrent builds have OOM'd the
  box.
- **Disk:** was ~91% full — run `docker system prune -af` before builds.
- **API token:** `~/.coolify-token` on the box; server UUID
  `azqkiidl028fi9yqbf7wg7nc`.
- **Env vars live only in Coolify** — never in this repo. (The demo needs
  none; `MOOTLOOP_DEMO_VAULT` is baked into the image.)

### DEV app

- Source: public repo `https://github.com/damienriehl/mootloop`, branch
  `main`, dockerfile buildpack, port 8000.
- Domain: `mootloop.dev.openlegalstandard.org`.
- **Auto-deploy:** the Coolify GitHub App (`coolify-alea-dev`) covers only the
  `alea-institute` org, *not* `damienriehl/mootloop`. Use the public-repo app
  type plus a **plain GitHub webhook** pointed at the app's Coolify manual
  webhook endpoint (webhook URL + secret from the Coolify app's settings).

### PROD app

- Domain: `mootloop.org`.
- **Deploys are manual and ask-gated** — always confirm with Damien before a
  prod deploy (house rule).

### DNS (Cloudflare)

- A records for `mootloop.dev.openlegalstandard.org` and `mootloop.org` → the
  box, **DNS-only** (grey cloud) so Coolify's Let's Encrypt flow issues
  certificates directly.

## Runtime contract

| Aspect | Value |
| --- | --- |
| Port | `${PORT:-8000}` |
| Health | `GET /health` |
| Secrets | none |
| Writes | none (bake happened at build time) |
| Data | synthetic fixture matter only |
