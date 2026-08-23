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

## A-02 — Restore controlled-browser access

**Owner:** Damien for reinstall and authentication; agent for browser QA

**Status:** Open; sole remaining U-17A tail

1. Reinstall the Browser plugin from the ChatGPT plugin UI. The currently installed
   control client fails during initialization before tab discovery.
2. Complete Cloudflare Access in the controlled Chrome profile. Credentials and
   one-time codes remain human-only.
3. Tell the agent when the authenticated synthetic route is open. The agent can then
   perform the content-free phone-width journey and close U-17A.

## Subsequent human gates

- U-17B requires a new, explicit D-03 authorization before any named protected-matter
  read or run. This handoff grants none.
- U-17C requires the attorney's quality/confidentiality verdict after the controlled
  comparison is prepared.
- D-09 requires approval of a concrete least-privilege immutable remote sink before
  the off-box evidence clause can be closed.

U-11B and U-12 through U-16 remain blocked until the U-17A -> U-17B -> U-17C
clean-compounding-loop sequence is complete under D-10/D-16. D-09 independently
gates only the off-box evidence clause. Until the clean compounding loop is complete,
the agent may maintain documentation and verify repository-only state but must not
start those breadth units.
