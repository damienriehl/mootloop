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

**Status:** Browser control restored on 2026-08-23; authentication remains open and is
the sole remaining U-17A tail

Chrome DevTools control is working and the exact synthetic U-17A run route is open at
the Cloudflare Access login page. The Browser-plugin client still lacks its trusted
Node service, but that no longer blocks QA because the user explicitly approved Chrome
DevTools as the control path. The Access page itself has passed a 390-by-844, DPR-3
phone-width overflow and accessibility-tree check; only the authenticated application
journey remains.

1. Complete Cloudflare Access in the controlled Chrome profile. Credentials and
   one-time codes remain human-only.
2. Tell the agent when the authenticated synthetic route is open. The agent can then
   perform the content-free phone-width journey and close U-17A.

## Subsequent human gates

- U-17B requires a new, explicit D-03 authorization before any named protected-matter
  read or run. This handoff grants none.
- U-17C requires the attorney's quality/confidentiality verdict after the controlled
  comparison is prepared.
- D-09 requires the provider/retention choice in
  `docs/decisions/2026-08-23-d09-remote-signed-head-sink.md` before the content-free
  signed-head integrity ledger can be implemented. That approval does not authorize
  or close FD6-01's separate encrypted off-box backup destination and restore drill.
- FD6-01 requires its own encrypted-backup provider, retention, and provisioning
  approval before any remote backup write or restore drill.

U-11B and U-12 through U-16 remain blocked until the U-17A -> U-17B -> U-17C
clean-compounding-loop sequence is complete under D-10/D-16. D-09 independently
gates only the content-free signed-head ledger; FD6-01 remains a separate provider,
provisioning, and restore decision. Until the clean compounding loop is complete, the
agent may maintain documentation and verify repository-only state but must not start
those breadth units.
