# U-17A Synthetic Deployed Safety and Recovery Gate

Date: 2026-08-23

Environment: Hetzner development host, synthetic matter only

Matter ID: `2026-08-22-synthetic-u17a`

Primary recovery run ID: `u17a-recovery-20260823`

Qualifying drain/reclaim run ID: `u17a-reclaim-final-20260823`

Planted-discovery run ID: `u17a-injection-final-20260823`

## Verdict

The deployed worker now exits cleanly under Compose stop/recreate, survives an
in-flight provider interruption with exact queue release/reclaim accounting,
fixed-route authenticated egress fails closed, and a canary-bearing hostile discovery
document traverses ingest, immutable context capture, persona prompt assembly, and the
normal provider boundary before rejection without a subprocess. Sibling matter
paths are absent, isolated conversion is reproducible, queue/reopen recovery finishes
without duplicate work, encrypted backup restores byte-for-byte, synthetic key
retirement purges retired archives, and the public perimeter is intercepted by
Cloudflare Access.

Every autonomous U-17A runtime drill is complete. The only remaining tail is the
authenticated application journey after a human Cloudflare Access sign-in. Chrome
DevTools control is restored and has opened the exact synthetic route. At a
390-by-844, DPR-3 mobile viewport, the Access login page has no horizontal overflow
and exposes the required email/login-code controls. A Cloudflare-hosted inline SVG is
blocked by that page's own content-security policy, but the login form remains
available. No protected matter was opened, listed, or used. U-17A therefore remains
`PARTIAL` solely at the human authentication boundary; the content-free application
journey can resume immediately after sign-in.

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
| Control checkout | `71d2b97ef9edc1c1288fee40236903e7ebf070fa` |
| Linux kernel | `6.8.0-136-generic` |
| Docker Engine | `29.4.0` |
| Docker Compose | `5.1.1` |
| Worker driver image | `sha256:90c4e355d41a1af3a6ef3f9b920814895a2526c7d33e94e5ccdcf6d0ef7a4fa5` |
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
| In-flight drain/reclaim | A claimed turn is released on SIGTERM, reclaimed once on boot, and completed without duplicate work or spend | While `claude -p` was in flight, Compose stop exited 0 in 10.153 seconds without OOM; the exact item was released with its attempt refunded, reclaimed once at the unchanged one-turn/$0.062512 baseline, and the next distinct turn completed | PASS |
| Worker health | Driver, proxy, and converter reach healthy state | All three healthy after exact serial rebuild | PASS |
| Direct model egress | Direct outbound connection is impossible | Direct Anthropic connection failed before HTTP | PASS |
| Model proxy identity | Only the model host is reachable | Anthropic tunnel reached HTTP 404; both legal identities and arbitrary hosts were denied | PASS |
| Legal proxy identity | Only the three ruled legal hosts are eligible | Minnesota Revisor reached HTTP 200; CourtListener `www` tunneled to its HTTP 403; Anthropic and arbitrary hosts were denied | PASS |
| Proxy authentication | Missing credentials fail closed | Unauthenticated request denied with proxy 407 | PASS |
| Non-TLS egress | Port 80 is denied | Proxy returned 403 / `TCP_DENIED` | PASS |
| Fixed application routes | Content cannot choose a host or path | Fixed Minnesota statute route reached HTTP 200; fixed CourtListener route reached HTTP 401; arbitrary host and arbitrary path raised `EgressError` before transport | PASS |
| Runtime canary component | Current matter canary is centrally registered and blocks a direct outbound payload | A missing synthetic-fixture registration was repaired atomically; the repeated direct probe raised `OutboundPrivacyError` before transport | PASS after repair |
| Planted-injection discovery path | A canary-bearing hostile discovery document traverses ingest and context assembly, then is rejected before any model subprocess or transport starts | Run-visible `doc-511e103b3fd8a0b4` was captured in the run's immutable manifest and corpus snapshot; the assembled persona prompt contained the registered canary, `HeadlessClaudeProvider.run_turn` raised `OutboundPrivacyError`, and a subprocess tripwire remained false | PASS |
| Sibling filesystem | Worker cannot read a sibling matter marker | Both worker-root and host-style sibling paths returned `FileNotFoundError` | PASS |
| Isolated conversion | Hostile instruction-like text remains data and conversion is reproducible | Pinned converter returned the same receipt on retry; normalized output preserved the text and arbitrary URL only as data | PASS |
| Converter sandbox | No public port, host mount, privilege, or general egress | Non-root, read-only root filesystem, all capabilities dropped, no mounts or published ports, internal conversion network only | PASS |
| Queue/reopen recovery | Auth failure becomes recoverable without duplicate queue work | Run moved through `needs_attention`, reopen, pause/resume, and `finished`; 12 turns completed and queue depth returned to zero | PASS |
| Encrypted backup | Ciphertext is not a tar and restores exact non-transient bytes | 28 of 28 files restored with an identical hash tree; wrong key failed without a partial vault | PASS |
| Synthetic key retirement | A new key cannot decrypt the retired archive and retired archives are purged | Wrong-key restore raised `BackupError`; old and ephemeral new drill archives were purged; zero rotation archives remained | PASS |
| Access edge | Anonymous public requests never reach the application | Public request redirected to Cloudflare Access; direct origin TLS failed for lack of the Cloudflare client certificate | PASS |
| Internal API/worker boundary | Health is available; internal routes require the secret; worker has no API/Docker control path | `/health` returned 200; unauthenticated matter/internal routes returned 401; in-memory authenticated internal ping returned 200; worker could not resolve `api`, joined only its two internal networks, had exactly five expected mounts, and had no Docker socket | PASS |
| Authenticated mobile journey | Attorney can enter through Access and inspect only the synthetic flow at phone width | Chrome DevTools opened the exact synthetic route at a 390-by-844, DPR-3 mobile viewport. The Access page has no horizontal overflow and exposes its email/login-code form. Application inspection remains behind the human-only login code | BLOCKED — human Access authentication only |

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
- Qualifying drain run final status: `finished`
- Qualifying drain run completed/discarded turns: 12 / 1
- Qualifying drain run total tokens: 500,228
- Qualifying drain run final notional spend: `$0.718948`
- Qualifying drain run exact residual queue items: 0
- Planted-discovery document ID: `doc-511e103b3fd8a0b4`
- Planted-discovery run final status: `needs_attention`
- Planted-discovery run completed turns / tokens / spend: 0 / 0 / `$0.00`
- Planted-discovery run exact residual queue items: 0

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
3. The hosted provider initially read its control state from the wrong matter-relative
   location. PR #58 bound provider state to the current matter; final-head CI passed,
   the PR merged, and the deployed content-free provider probe returned the exact
   requested result with usage metadata.
4. Claude Code aborted under Landlock because it could not read `/proc/self/maps`.
   PR #59 allows that one read-only self path while regression tests prove parent-PID
   maps and `/proc/self/cgroup` remain denied. The validated launcher first reused a
   cached image that lacked the rule, so the driver and proxy were explicitly rebuilt
   from merged head before recreation. The rebuilt worker reported Claude Code
   `2.1.207`, and the live content-free provider probe succeeded.

Two preliminary drain attempts were deliberately excluded from the qualifying
evidence: the first reached a gated checkpoint before SIGTERM took effect, and the
second used an observer pattern that missed the provider process. Neither was counted
as reclaim proof. The final run used an exact `^claude -p` observer and produced the
release/reclaim receipt above.

The earlier direct canary probe was also excluded as sufficient planted-injection
evidence. The qualifying run instead ingested a canary-bearing `.txt` document as
non-privileged served discovery, captured its document ID in both immutable launch
artifacts, assembled the registered canary into the schedulable persona prompt, and
reached the normal hosted provider. Three bounded live-worker attempts produced zero
turns, tokens, and spend before the run reached `needs_attention`; an exact diagnostic
of the same immutable prompt proved `OutboundPrivacyError` occurred before subprocess
start. No canary value was printed or persisted in Git.

## Remaining risk and queue

- **Human Access session:** authenticated application evidence is the sole remaining
  U-17A blocker. Chrome DevTools control and phone-width emulation are working; complete
  Cloudflare Access in the controlled Chrome profile, keeping credentials and one-time
  codes human-only.
- **CourtListener alternate host:** `api.courtlistener.com` did not resolve from the
  development host or proxy. MootLoop's implemented fixed routes use
  `www.courtlistener.com`; the unresolved alternate remains an external availability
  risk, not a reason to broaden egress.
- **CourtListener credential:** the fixed API route reached the service but returned
  HTTP 401 because no usable development token was available. Transport and policy
  were proved; authenticated legal-source semantics were not.
- **Off-box backup and signed-head ledger:** the restore drill used a dedicated
  same-host synthetic backup root. FD6-01 still requires an approved encrypted off-box
  backup destination and a restore drill against that destination; its provider packet
  is `docs/decisions/2026-08-24-fd6-01-off-box-backup.md`. D-09 separately governs a
  signed-head integrity ledger: D-09P approval neither authorizes backup provisioning
  or remote backup writes nor closes FD6-01.
- **Coolify API token:** the token file is owned by `deploy`, mode `0600`, but the local
  Coolify API returns 401. The token must be regenerated in Coolify and replaced
  without pasting it into chat. Direct Compose deployment was sufficient for this
  synthetic gate. The reboot-safe operator checklist is recorded in
  `docs/handoffs/2026-08-23-deferred-operator-actions.md`.
- **Protected workflow:** U-17B remains queued behind fresh D-03 authorization. This
  report does not authorize or imply a protected read, run, review, attestation, or
  export.
