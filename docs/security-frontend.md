# FE-0 Threat Model — Hosted Frontend Perimeter

Scope: the hosted MootLoop tier (`mootloop.damienriehl.com`) that puts real matter
data on a shared Coolify box. This is the FE-0 security foundation, built and gated
**before any real matter data touches the server**. It distills the plan's Security
architecture and deepening insights FD-1/FD-2/FD-3.

The demo tier (`src/mootloop/web/app.py`) is out of scope and stays read-only; nothing
here may weaken its `test_web_readonly` invariant.

## Assets

- Matter vaults — confidential client work product under `/srv/mootloop-matters/<id>/` (0700, outside every repo).
- `CLAUDE_CODE_OAUTH_TOKEN` — the Max-plan subscription token; the crown jewel (1-year lifetime, exfil = account takeover + spend).
- Google refresh token — durable Drive/Gmail credential; encrypted at rest.
- `AccessAuditEntry` log — the hash-chained who/when/what record of every matter-data page view and download; its integrity is itself an asset.
- Driver internal secret — the bearer/mTLS secret the driver presents to the internal write API (replaces localhost trust).
- Traefik AOP client key — the per-hostname Authenticated Origin Pull certificate/key.

## Trust zones and the boundary crossed at each

- Browser — fully untrusted; the only client. Boundary: nothing reaches origin without a valid Cloudflare Access session.
- Edge (Cloudflare Access) — issues/validates the Access JWT and terminates TLS. Boundary crossed: public internet -> authenticated session; unauthenticated requests never leave the edge.
- BFF (Next.js, Node runtime) — the single Access-verified surface; mirrors JWT verification in `middleware.ts` and proxies to the API. Boundary: authenticated user -> internal network; it holds no vault access and does no domain computation (BFF-is-thin invariant).
- API (FastAPI, fully internal, own Docker network, unpublished) — verifies the Access JWT dependency AND the driver/BFF internal secret; all routes are per-matter. Boundary: request -> service functions; a sibling container on the shared network cannot reach it.
- Driver (worker service) — runs the engine (`claude -p`), watchers, scheduler. Boundary: orchestrator tick -> `claude -p` persona turn inside the sandbox; holds secrets, never exposes them to persona turns.
- Vault (filesystem) — `safe_vault_path` realpath-containment choke-point and the `MatterRegistry` resolver. Boundary: `matter_id` (untrusted) -> vault path only after charset + realpath-containment checks; per-matter OS isolation limits the engine's filesystem view.

## Layered perimeter

Three independent layers; each stops a distinct class of attack. Defense in depth — no single layer is the boundary.

1. Cloudflare Access JWT + origin validation.
   - What it is: self-hosted Access apps on BOTH the UI hostname and the API paths (an Access app on one path does not cover others). FastAPI verifies `Cf-Access-Jwt-Assertion` on every request — RS256 (algorithm asserted, never read from the token) against the cached team JWKS, pinning `aud` (per-app AUD), `iss`, `exp`, and the expected email; JWKS fetch failure = reject.
   - What it stops: unauthenticated access, cross-app JWT reuse (wrong `aud`), forged/`alg=none` tokens, and service tokens on matter routes (they carry no email claim, so they are rejected).

2. Per-hostname Authenticated Origin Pulls (AOP).
   - What it is: an uploaded per-hostname client certificate enforced as a Traefik `tls.options` attached only to this vhost (NOT global AOP — the box's other public apps stay open).
   - What it stops: direct-to-origin requests that bypass Cloudflare — an attacker who finds the origin IP cannot present the client cert, so the TLS handshake fails before any app code runs.

3. Internal driver secret.
   - What it is: a dedicated bearer/mTLS secret the driver and BFF present to FastAPI's internal write paths; the API container is not published on the shared Docker network.
   - What it stops: lateral movement — a compromised sibling app on the same Coolify Docker network cannot reach or write the matter API even from "localhost", because localhost trust is dead on a shared network.

## Engine sandbox requirements (FD-1)

- No Bash / no WebFetch / no WebSearch on persona turns — `--allowedTools` restricted to read-only file tools (personas draft text). `--settings` deny/allow is defense-in-depth, never the boundary.
- Network-egress jail per turn (namespace/bwrap or egress proxy) permitting only `api.anthropic.com` — neutralizes token/vault exfil even from an injected discovery document.
- Per-matter OS isolation in v1 (per-matter UID or ephemeral container mounting only that matter's vault) — the ethical wall applies to the engine's filesystem view, not just learnings.
- No matter-tier container mounts the Docker socket.
- Gate (FE-1): a planted-injection-in-discovery fixture attempting token/vault exfil must fail closed.

## Controls that do NOT carry over

The existing deterministic-core controls protect the CLI/vault; none of them automatically protects the hosted network tier. Each needs a hosted analogue.

| Existing control | Why it does not carry | What FE-0 (and its FD-3 analogue) adds |
| --- | --- | --- |
| `redact()` patterns | Shaped for CLI/log output; the hosted tier has new outbound sinks (audit log, SSE, ntfy, digest) the patterns never saw. | Add Google-refresh (`1//…`), OAuth-token shapes, and exact live secret values; apply `redact()` at every new sink. |
| Runtime canary tripwire | The build-time `privacy_grep` scans files at commit; it cannot see live outbound network/notification payloads. | Runtime tripwire: the per-matter canary must never appear in any outbound network or notification payload; emitting it fails closed. |
| Hash-chained access audit | The CLI has no per-request access surface, so there is no existing who/when/what audit for page views or downloads. | `AccessAuditEntry` hash-chained, its head folded into the attestation tuple; downloads fail closed if the audit write fails; append-only via a different-user file or off-box sink. |

## FE-0 penetration checklist (testable assertions)

Each maps to code landed in units 2/3 and must pass before real matter data is loaded.

1. An attacker hitting the origin IP directly (bypassing Cloudflare) is blocked by per-hostname AOP: the TLS handshake fails with no client cert.
2. An attacker presenting no Access JWT on any matter route is blocked by the FastAPI Access dependency (401/403).
3. An attacker replaying a valid JWT minted for a different Access app is blocked by `aud` pinning (wrong AUD -> reject).
4. An attacker submitting an `alg=none` or RS256-forged token is blocked because the verifier asserts RS256 and validates the signature against the pinned JWKS; a JWKS fetch failure also rejects.
5. An attacker presenting a Cloudflare service token (no email claim) on a matter route is rejected — service tokens are barred from matter routes.
6. An attacker passing `matter_id = "../other"` (or `.`, `..`, a path separator, or an absolute path) is blocked by `MatterRegistry.resolve` charset validation (`validate_id`) before any filesystem access.
7. An attacker via a symlinked matter directory pointing outside the matters-root is blocked by the realpath-containment assertion in `resolve`/`list_matters` (`VaultBoundaryError`, fail-closed).
8. A request for a non-existent `matter_id` yields `MatterNotFoundError` (404), never a stack trace or path disclosure.
9. A compromised sibling container on the shared Docker network cannot reach the write API — it is unpublished and requires the internal driver secret.
10. No matter-tier container mounts the Docker socket.
11. An upload with a client-supplied filename cannot control the on-disk path: names are metadata only; the stored path is a UUID resolved through `safe_vault_path` (path-traversal and zip-path-traversal rejected).
12. Exceeding the app-side rate limit on upload / run-start / inference endpoints is throttled by the ASGI rate-limit middleware (not only the edge).
13. A download whose `AccessAuditEntry` write fails is refused — audit failure fails the download closed, not open.
