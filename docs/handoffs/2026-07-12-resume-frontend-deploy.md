# HANDOFF — MootLoop frontend deploy (resume here)

**State (2026-07-12):** PRs #1–14 merged on `main`. Pipeline v1 complete; public demo LIVE at
https://mootloop.dev.openlegalstandard.org (Coolify, webhook auto-deploy). Hosted matter-tier
CODE complete: FE-0 perimeter, FE-1 sandboxed engine, FE-2 cockpit+inbox (`frontend/`),
FE-2.5 freeform on-ramp + export. 467 py + 32 fe tests green. Plans: `docs/plans/2026-07-11-001`
(v1), `2026-07-11-002` (demo deploy, completed), `2026-07-12-001` (frontend, ACTIVE — FD-10 is
the sequence). Threat model + pen checklist: `docs/security-frontend.md`.

## Immediately executable (unblocked by Damien 2026-07-12)

1. **Cloudflare** — token in `~/.mootloop/secrets.env` (`CLOUDFLARE_MOOTLOOP_TOKEN`; scopes:
   DNS edit damienriehl.com + Access apps edit). Account `05b5e8e80e510bd7cf1f08cb78ae9d07`,
   zone damienriehl.com `45539317ebc2598f913b867756fa58ea`, ZT team
   `young-unit-68fd.cloudflareaccess.com`. Do: (a) proxied `A mootloop → 204.168.246.227`;
   (b) Access self-hosted apps for mootloop.damienriehl.com (UI; and API paths if not BFF-only)
   with Allow policy email `damienriehl@gmail.com`, 24h session — record each app's **AUD tag**
   (needed for `CF_ACCESS_AUD` env); (c) try `POST /zones` for mootloop.org (if scope allows,
   Damien then flips nameservers at his registrar; else he adds A records there —
   `@` and `www` → 204.168.246.227).
2. **pandoc local** — try `sudo -n apt-get install -y pandoc`; if password needed, Damien types
   `! sudo apt-get install -y pandoc`.
3. **Fence matter seed** — source is a Google **Drive folder** (link in `~/.mootloop/secrets.env`).
   Pull via Drive MCP or rclone → rsync to server vault at seed time (P-34).

## Deploy chain (after 1)

Coolify (box `hetzner-dev`, token `~/.coolify-token`, server `azqkiidl028fi9yqbf7wg7nc`,
project mootloop `p5hcn07bhqx0h8eb0vhyia94`; demo apps: dev `dxr2q6…`, prod `wx0ow6…`):
create matter-tier apps (frontend BFF + api internal + driver worker; FD-5 topology — FastAPI
NOT publicly routed; internal secret `MOOTLOOP_INTERNAL_SECRET`), env in Coolify only, serial
builds, disk check. Then per-hostname AOP (FD-2; hand-edit Traefik dynamic config), pen-gate
(13 items in security-frontend.md) — **no matter data until pen-gate + FD-6 gates pass**
(hosted backup restore drill; NOTE: `mootloop close` inventory does NOT exist yet — FD-6 gate
item still to build). Then: `claude setup-token` on box (WAITING ON DAMIEN), seed vault, first
live run (FE-7).

## Damien reminders (he asked to be reminded)
- `claude setup-token` → box secrets (blocks engine on server)
- CourtListener token → secrets (blocks live citation gate)
- Google OAuth app → "In Production" (blocks FE-5 Drive watcher only)
- mootloop.org nameservers/records (demo PROD)

## Standing rules
CE end-to-end; Fable orchestrates, Opus performs; every review deliverable = interactive HTML
artifact (with the clipboard execCommand fallback — see memory feedback_artifact_clipboard);
branch per phase → PR → verify → merge; matter data NEVER in repo.
