# HANDOFF — MootLoop matter tier DEPLOYED, pen-gate GREEN (resume here)

**State (2026-07-13, session 2 cont.):** The hosted matter tier is **live and healthy** at
https://mootloop.damienriehl.com behind Cloudflare Access, with **per-hostname AOP live**
— only Cloudflare reaches the origin (direct-to-origin fails the TLS handshake; verified).
FE-0 penetration gate is **GREEN**: all 13 assertions hold (7 live-verified, 6 code-gated,
0 failing/blocked). Attestation (interactive): `docs/evidence/fe-0-pen-gate.html`
(published artifact + versioned in repo). PRs #15 (deploy infra), #16 (auth-order fix),
#17 (evidence pack), #19 (AOP attach) merged to `main`.

**AOP specifics (done):** client cert uploaded to CF per-hostname store (cert_id
`73d666f6-4d78-4559-a13d-15478b3dfb50`, binding active); Traefik router carries
`tls.options=mootloop-aop@file` via a compose label (router name is app-UUID-derived —
re-derive if the app is recreated). CF token now has SSL/Certificates edit scope.

**Coolify API token was rotated** (the old `/home/deploy/.coolify-token` expired
mid-session; replaced with a fresh read-write token — deploys work again).

## What is deployed

- Coolify app `mootloop-matter` (uuid `op3exbmxe1q21qyy69tlngk9`) in project `mootloop`
  (`p5hcn07bhqx0h8eb0vhyia94`) on server `azqkiidl028fi9yqbf7wg7nc`. Three services, all
  healthy: **web** (Next.js BFF, public, `mootloop.damienriehl.com`, LE cert issued),
  **api** (FastAPI, internal/unpublished), **driver** (engine worker). Compose:
  `docker-compose.matter.yaml`; images `Dockerfile.matter-api` + `Dockerfile.driver`
  (claude-code pinned `2.1.207`, bubblewrap egress jail, uid/gid 3200).
- **Box (204.168.246.227):** service user `mootloop` (uid 3200), `/srv/mootloop-matters`
  (0700), `~mootloop/.mootloop/secrets.env` (0600) holding `MOOTLOOP_INTERNAL_SECRET` +
  `MOOTLOOP_DOWNLOAD_SIGNING_KEY` (pre-seeded; the container mount is read-only so
  first-use derivation can't write). Docker pruned (was 87% → 70%).
- **Cloudflare:** proxied `A mootloop → 204.168.246.227`; Access app "MootLoop Matter
  Tier" (AUD `0930916199c5086d48182201764ae6bb45f6f68be1e6568ab82ebc798705a177`, allow
  `damienriehl@gmail.com`, 24h) → env `CF_ACCESS_AUD`; ACME-challenge bypass app so LE
  can validate behind Access. Coolify env vars set (names in `docs/deploy-matter.md`).

## Blocked on Damien (remaining — perimeter is DONE)

1. **Engine token — still needed for any live run.** `claude setup-token` must produce a
   value starting `sk-ant-oat01-…`; write it to `~mootloop/.mootloop/secrets.env` as
   `CLAUDE_CODE_OAUTH_TOKEN` (0600, service-user owned). (2026-07-13: Damien pasted a
   non-`sk-ant-oat` value that was NOT written — re-run and confirm the prefix.)
2. mootloop.org demo-prod DNS (Namecheap): no Namecheap API key on the box; needs API
   access enabled + key + API user + IP allowlist (204.168.246.227, 97.116.181.129) → drop
   at `~/.secrets/namecheap` for autonomous DNS. Else Damien adds A records `@`/`www` →
   204.168.246.227 by hand. Low priority (demo-prod only).
3. CourtListener token (live citation gate); Google OAuth → "In Production" (FE-5 only).
4. **FD-6 gates before matter data** (not credential-blocked — to build/run): hosted
   backup-restore drill; `mootloop close` inventory (does not exist yet).

## What the gate caught (fixed)

Auth-before-resolve: every per-matter route resolved the vault before checking the Access
token, leaking an unauthenticated 400/404 matter-ID oracle. PR #16 reorders auth ahead of
`resolve_matter` on all 19 routes + adds a no-oracle regression test. Re-verified live:
existent/nonexistent/charset-invalid ids all return an identical 401.

## Next (after AOP unblocked + setup-token)

Attach the AOP `tls.options` to the web router → re-run pen item 1 (direct-origin TLS
handshake must fail) → flip the gate to fully green → FD-6 gates (hosted backup restore
drill; `mootloop close` inventory — still to build) → SSH-seed the fence matter
(Drive folder in `~/.mootloop/secrets.env`) → first live hosted run (FE-7).

## Standing rules
CE end-to-end; Fable orchestrates, Opus performs; review deliverables = interactive HTML
artifact with the execCommand clipboard fallback; branch → PR → verify → merge; matter
data NEVER in repo; no matter data on the server until the pen-gate is fully green.
