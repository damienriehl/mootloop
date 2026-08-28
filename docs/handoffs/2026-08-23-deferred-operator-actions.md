# Deferred Operator Actions

Date recorded: 2026-08-23

This handoff preserves the human-only actions that currently gate the plan-completion
queue. It contains no credentials, protected matter identifiers, or matter content.

## A-01 — Restore Coolify API access

**Owner:** Damien

**Status:** Deferred by owner on 2026-08-23

**Why it remains open:** `/home/deploy/.coolify-token` on `hetzner-dev` is owned by
`deploy` with mode `0600`, but the local Coolify API returns HTTP 401. Direct Compose
control was sufficient for the completed synthetic U-17A gate, so token repair was
not required to finish that evidence.

When ready:

1. Open `https://coolify.dev.openlegalstandard.org/security/api-tokens` in an
   authenticated browser session.
2. Revoke the rejected token if it is still listed, then create a replacement with
   the narrowest application-scoped, read-only permissions Coolify offers. If Coolify
   cannot issue a read-only token, stop and obtain explicit approval for the named
   mutation scope before creating a management-capable token.
3. Store the replacement in the OS keychain or
   `/home/deploy/.mootloop/secrets.env`, never the legacy
   `/home/deploy/.coolify-token`. Do not paste it into chat, a shell command line,
   logs, Git, or a matter vault. Keep any secrets file owned by `deploy`, mode `0600`.
   If existing tooling depends on the legacy path, update that tooling in a separate
   reviewed change before using the replacement.
4. Ask the agent to verify Coolify API authentication without printing the token and
   resume read-only Coolify-managed deployment inspection.

**Done when:** a content-free local API request authenticates successfully, the old
token is unusable, and the replacement remains in an approved secret store readable
only by `deploy`.

## A-02 — Authenticate the restored controlled browser

**Owner:** Damien for authentication; agent for browser QA

**Status:** Complete for the current controlled Chrome profile; no operator action is
currently required

**Last rechecked:** 2026-08-28. The controlled Chrome profile reached the exact
synthetic U-17A route through Cloudflare Access without another login prompt. The
agent did not enter credentials, request a code, inspect browser storage, or invoke an
application mutation.

The authenticated 390-by-844 phone-width journey reached the run cockpit, Begin Task,
Decision Inbox, and Export room without invoking a button or substantive attorney
action. Those rooms did not overflow. The Runs index did overflow to 590 pixels; its
local fix and regression test are recorded in
`docs/audits/2026-08-28-readonly-state-confirmation.md` and await a separately
authorized delivery/deployment before U-17A can close.

If the Access session expires before the post-deployment recheck, authenticate the
same controlled Chrome profile again and tell the agent when it is ready. Credentials
and one-time codes remain human-only.

## A-03 — Rotate and relocate the Namecheap registrar credential

**Owner:** Damien

**Status:** Open — historical credential exposure and nonstandard storage

The historical deployment handoff records that the Namecheap API key used for
`mootloop.org` DNS was pasted into chat and stored outside MootLoop's approved secret
locations. D-08's 2026-08-24 read-only check proves that apex and `www` TLS, health,
and the public synthetic demo now work, so no DNS, certificate, or redeploy mutation is
needed. It does not make the exposed key safe.

When ready:

1. Revoke or rotate the exposed Namecheap API key through Namecheap's authenticated
   account controls. Do not paste the old or new value into chat or a command line.
2. Store the replacement in the OS keychain or `~/.mootloop/secrets.env`, mode `0600`;
   do not retain it in the historical `~/.secrets/namecheap` file.
3. Preserve the narrow existing source-IP allowlist and DNS-only scope. Before any
   future DNS write, read the complete record set because Namecheap `setHosts` replaces
   all host records.
4. Remove the obsolete credential file only after the replacement is verified without
   printing the value.

**Done when:** the old key is unusable, the new key is held only in an approved secret
store, a content-free authenticated Namecheap read succeeds, and no DNS record changed.

## Subsequent human gates

- U-17B requires a new, explicit D-03 authorization before any named protected-matter
  read or run. This handoff grants none.
- U-17C requires the attorney's quality/confidentiality verdict after the controlled
  comparison is prepared.
- D-09 requires the provider/retention choice in
  `docs/decisions/2026-08-23-d09-remote-signed-head-sink.md` before the content-free
  signed-head integrity ledger can be implemented. That approval does not authorize
  or close FD6-01's separate encrypted off-box backup destination and restore drill.
- FD6-01 requires the provider/retention choice in
  `docs/decisions/2026-08-24-fd6-01-off-box-backup.md` before implementation,
  provisioning, any remote backup write, or restore drill.

U-11B and U-12 through U-16 remain blocked until the U-17A -> U-17B -> U-17C
clean-compounding-loop sequence is complete under D-10/D-16. D-09 independently
gates only the content-free signed-head ledger; FD6-01 remains a separate provider,
provisioning, and restore decision. Until the clean compounding loop is complete, the
agent may maintain documentation and verify repository-only state but must not start
those breadth units.
