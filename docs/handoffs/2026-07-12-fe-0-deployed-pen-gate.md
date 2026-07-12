# HANDOFF — MootLoop matter tier DEPLOYED, pen-gate 11/13 (resume here)

**State (2026-07-12, session 2):** The hosted matter tier is **live and healthy** at
https://mootloop.damienriehl.com behind Cloudflare Access. FE-0 penetration gate: **11
PASS, 0 FAIL, 2 BLOCKED on operator credentials.** Attestation (interactive):
`docs/evidence/fe-0-pen-gate.html` (published artifact + versioned in repo). PRs #15
(deploy infra), #16 (auth-order fix), #17 (evidence pack) all merged to `main`.

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

## Blocked on Damien (the 2 open pen-gate items + engine)

1. **AOP (pen item 1) — token scope.** Per-hostname Authenticated Origin Pulls is fully
   staged: CA + client cert at `/root/mootloop-aop/` on the box; Traefik v3.6
   `tls.options` at `/data/coolify/proxy/dynamic/mootloop-aop.yaml` (CA copied to
   `/traefik/certs-aop/`). Enforcement is INERT until (a) the client cert is uploaded to
   Cloudflare's per-hostname store — the MootLoop token lacks **Zone → SSL and
   Certificates → Edit** — and (b) the web router references
   `tls.options=mootloop-aop@file`. **Do NOT set RequireAndVerifyClientCert before CF
   presents the cert, or the live site breaks.** Interim: the origin already fails closed
   on auth (matter routes → 401 without a JWT), so no matter data leaks meanwhile.
2. **`claude setup-token`** on the box as the `mootloop` user (blocks the engine / any
   live run) — the crown-jewel OAuth token.
3. mootloop.org demo-prod: token can't create the zone (needs Zone→Create) — else Damien
   adds registrar A records `@`/`www` → 204.168.246.227.
4. CourtListener token (live citation gate); Google OAuth → "In Production" (FE-5 only).

The evidence pack's rulings section captures decisions on items 1–3; paste them back.

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
