# U-17A Synthetic Deployed Safety and Recovery Gate

Date: 2026-08-23

Environment: Hetzner development host, synthetic matter only

Matter ID: `2026-08-22-synthetic-u17a`

Run ID: `u17a-recovery-20260823`

## Verdict

The deployed worker now exits cleanly
under Compose stop/recreate, fixed-route authenticated egress fails closed, a planted
canary cannot leave the worker, sibling matter paths are absent, isolated conversion
is reproducible, queue/reopen recovery finishes without duplicate work, encrypted
backup restores byte-for-byte, synthetic key retirement purges retired archives, and
the public perimeter is intercepted by Cloudflare Access.

Three evidence tails remain: a full hostile-input/persona-turn trace that ends at the
blocked transport boundary, an in-flight stop with durable checkpoint/reclaim on boot,
and the authenticated mobile journey after a human Cloudflare Access sign-in. No
protected matter was opened, listed, or used. U-17A therefore remains `PARTIAL`; the
direct component probes below must not be promoted into broader end-to-end claims.

## Scope and human boundary

- Authorization covered synthetic deployed testing and runtime-only use of existing
  development secrets. Secret values were loaded only in memory and never printed,
  copied into a vault, or written to this report.
- No protected matter read or run occurred. U-17B still requires fresh authorization
  under D-03 before any real-matter access.
- No attorney decision, attestation, or substantive legal judgment was simulated.
- No global backup key was rotated. The rotation/purge behavior was proved with
  isolated process-only synthetic keys so protected archives could not be affected.
- No screenshot or matter content is retained in Git. Evidence below is content-free
  operational metadata.

## Environment lock

| Component | Observed version or immutable identity |
|---|---|
| Control checkout | `eb4fd08aa44035f8367798d0ad728f0d9e028483` |
| Linux kernel | `6.8.0-136-generic` |
| Docker Engine | `29.4.0` |
| Docker Compose | `5.1.1` |
| Worker driver image | `sha256:81dae55498f365b47552b2bf1bf1ea179bdd4ef024266f0d0edba8d91ca57362` |
| Egress proxy image | `sha256:43807e876be4168217efe89904e726da22bf1d45c27c4df50f6bd348b224a88f` |
| `folio-enrich` image | `sha256:584774699e50b5f7c95d147a896664c5c30dbcbeed727dfe0d5254715068916b` |
| Reviewed converter commit | `f5364365346d93a3aa01fd5fecf219090afe5410` |
| Context manifest SHA-256 | `3de04209c75f8179e231eb95655b8de5b82931e770f9aedd989625c5a3c8eedf` |
| Corpus manifest SHA-256 | `68612d55a9d894e0cb948e3dfe9612b4c9e95c0ac61ba12f2ffec926e2ca1db3` |
| Conversion receipt file SHA-256 | `164ed8226973487c061f9ef798970f2bf3abb8cea8ba650859d1ae6668eb0632` |

## Expected versus actual

| Gate | Expected | Actual | Result |
|---|---|---|---|
| Worker PID 1 | Runtime executable owns PID 1; no parent shell | `mootloop` owned PID 1 and the stored command used `exec` | PASS |
| Graceful termination | Compose stop exits within the grace period without SIGKILL/137 | Stop completed in 1.25 seconds; exit 0; `OOM=false` | PASS |
| Recreate/signal delivery | Recreate does not force-kill the worker | Recreate completed in 3.26 seconds; later queue-drill stop completed in 1.56 seconds | PASS |
| In-flight drain/reclaim | A claimed turn checkpoints or finishes on SIGTERM and remaining work is reclaimed on boot | No turn was deliberately held in flight during the stop timing probe | OPEN — separate controlled drill |
| Worker health | Driver, proxy, and converter reach healthy state | All three healthy after exact serial rebuild | PASS |
| Direct model egress | Direct outbound connection is impossible | Direct Anthropic connection failed before HTTP | PASS |
| Model proxy identity | Only the model host is reachable | Anthropic tunnel reached HTTP 404; both legal identities and arbitrary hosts were denied | PASS |
| Legal proxy identity | Only the three ruled legal hosts are eligible | Minnesota Revisor reached HTTP 200; CourtListener `www` tunneled to its HTTP 403; Anthropic and arbitrary hosts were denied | PASS |
| Proxy authentication | Missing credentials fail closed | Unauthenticated request denied with proxy 407 | PASS |
| Non-TLS egress | Port 80 is denied | Proxy returned 403 / `TCP_DENIED` | PASS |
| Fixed application routes | Content cannot choose a host or path | Fixed Minnesota statute route reached HTTP 200; fixed CourtListener route reached HTTP 401; arbitrary host and arbitrary path raised `EgressError` before transport | PASS |
| Runtime canary component | Current matter canary is centrally registered and blocks a direct outbound payload | A missing synthetic-fixture registration was repaired atomically; the repeated direct probe raised `OutboundPrivacyError` before transport | PASS after repair |
| Planted-injection execution path | Hostile matter input traverses the normal persona/run path and any attempted exfiltration is blocked before transport | Hostile instruction-like input was converted as data, but no live persona turn was driven from that fixture | OPEN — end-to-end trace required |
| Sibling filesystem | Worker cannot read a sibling matter marker | Both worker-root and host-style sibling paths returned `FileNotFoundError` | PASS |
| Isolated conversion | Hostile instruction-like text remains data and conversion is reproducible | Pinned converter returned the same receipt on retry; normalized output preserved the text and arbitrary URL only as data | PASS |
| Converter sandbox | No public port, host mount, privilege, or general egress | Non-root, read-only root filesystem, all capabilities dropped, no mounts or published ports, internal conversion network only | PASS |
| Queue/reopen recovery | Auth failure becomes recoverable without duplicate queue work | Run moved through `needs_attention`, reopen, pause/resume, and `finished`; 12 turns completed and queue depth returned to zero | PASS |
| Encrypted backup | Ciphertext is not a tar and restores exact non-transient bytes | 28 of 28 files restored with an identical hash tree; wrong key failed without a partial vault | PASS |
| Synthetic key retirement | A new key cannot decrypt the retired archive and retired archives are purged | Wrong-key restore raised `BackupError`; old and ephemeral new drill archives were purged; zero rotation archives remained | PASS |
| Access edge | Anonymous public requests never reach the application | Public request redirected to Cloudflare Access; direct origin TLS failed for lack of the Cloudflare client certificate | PASS |
| Internal API/worker boundary | Health is available; internal routes require the secret; worker has no API/Docker control path | `/health` returned 200; unauthenticated matter/internal routes returned 401; in-memory authenticated internal ping returned 200; worker could not resolve `api`, joined only its two internal networks, had exactly five expected mounts, and had no Docker socket | PASS |
| Authenticated mobile journey | Attorney can enter through Access and inspect only the synthetic flow at phone width | Controlled Chrome reached the Access login; human sign-in is pending | BLOCKED — human auth |

## Conversion and recovery receipts

- Synthetic document ID: `doc-6e2cdda92c57ea95`
- Conversion ID: `conversion-9bf858427dab003ed4080eb0`
- Normalized output SHA-256 was observed only as the abbreviated operational value
  `ed49003e…`; use the exact committed receipt-file SHA-256 above as the durable
  evidence commitment.
- Final run status: `finished`
- Completed turns: 12
- Final synthetic queue depth: 0
- Current canary registration: present and mapped to the synthetic matter

## PR #30 / #31 monitoring ledger

| Change | Current deployed observation | Status |
|---|---|---|
| PR #30 — execution, evidence, and export boundaries | Isolation, graceful shutdown, manifest binding, conversion, backup/restore, and queue durability passed under the synthetic gate | One qualifying synthetic operation recorded; no duplicate turns, lock/spend anomaly, or export-attestation claim observed |
| PR #31 — first-class reopen | Injected authentication failure produced `needs_attention`; reopen repaired the queue; pause/resume then finished with zero queued items | One qualifying reopen operation recorded; no reopen-loop or duplicate queue work observed |

The historical GitHub Actions job payload for the 2026-08-06 merge heads is no longer
available through the current connector. Both PRs are merged, and the deployed
behavior above tests their operational contracts rather than reconstructing stale CI.

## Repairs made during the gate

1. The deployed image inherited a shell as PID 1, so Compose stop waited for the grace
   deadline. PR #56 changed the driver entrypoint to `exec`; exact-head CI passed, the
   PR merged, and deployed stop/recreate timings now prove clean signal forwarding.
   They do not by themselves prove an in-flight turn checkpoint and boot reclaim.
2. Synthetic fixture provisioning had created a matter canary but omitted its central
   registry entry. The current token-to-matter mapping was added atomically with mode
   `0600`, the worker was recreated to refresh the bind inode, and the outbound probe
   then failed closed before transport. Existing registry entries were preserved and
   never printed. This was an environment-fixture repair, not a code change.

## Remaining risk and queue

- **End-to-end planted injection:** the direct registered-canary probe proves the
  privacy control, and hostile conversion proves instruction-like input remains data,
  but no normal persona/run path joined those observations. FD1-05 remains partial
  until a controlled synthetic hostile-input turn ends at the blocked transport seam.
- **In-flight drain/reclaim:** PID 1 and graceful stop are proved, but no turn was held
  in flight during SIGTERM. FD5-07's deployed drain/reclaim clause remains open until a
  controlled synthetic worker checkpoints or finishes and boot recovery reclaims the
  residual queue item.
- **Human Access session:** authenticated desktop/mobile browser evidence remains the
  final human-assisted U-17A blocker. A user must complete Cloudflare Access in the
  controlled Chrome profile; credentials and one-time codes stay human-only.
- **CourtListener alternate host:** `api.courtlistener.com` did not resolve from the
  development host or proxy. MootLoop's implemented fixed routes use
  `www.courtlistener.com`; the unresolved alternate remains an external availability
  risk, not a reason to broaden egress.
- **CourtListener credential:** the fixed API route reached the service but returned
  HTTP 401 because no usable development token was available. Transport and policy
  were proved; authenticated legal-source semantics were not.
- **Off-box evidence sink:** the restore drill used a dedicated same-host synthetic
  backup root. An approved immutable remote signed-head/backup sink remains D-09 work,
  so FD6-01's off-box clause is still open even though U-17A's restore and rotation
  behavior passed.
- **Coolify API token:** the token file is owned by `deploy`, mode `0600`, but the local
  Coolify API returns 401. The token must be regenerated in Coolify and replaced
  without pasting it into chat. Direct Compose deployment was sufficient for this
  synthetic gate.
- **Protected workflow:** U-17B remains queued behind fresh D-03 authorization. This
  report does not authorize or imply a protected read, run, review, attestation, or
  export.
