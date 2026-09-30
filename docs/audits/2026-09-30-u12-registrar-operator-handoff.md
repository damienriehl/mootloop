# Registrar rotation — operator handoff, 2026-09-30

Disposition: **SKIPPED — Damien hands-on**. Damien's September 30 answer says
that the Namecheap registrar key is not yet rotated and remains his console action.
The historical A-03 operator gate in
`docs/audits/2026-08-28-readonly-state-confirmation.md` agrees. No credential was
located, opened, copied, changed, or tested by this worker.

Minimal operator completion:

1. Rotate the registrar API key in Damien's authenticated Namecheap console.
2. Update the existing authorized consumer's secret by reference through its approved
   secret store; never put the value in repository documentation or a worker message.
3. From the allowlisted Hetzner IP, have the authorized operator run a read-only API
   check with the replacement key and confirm the old key is rejected. Record only
   timestamp, environment, sanitized success/failure, and operator confirmation.

A read-only check that the old key still works would require a network call and
credential access, both outside this worker's scope. No such check is claimed. No
credentials, API command with embedded secrets, or fabricated “already rotated”
receipt are supplied. The exact consumer and current provider procedure must be
confirmed by the operator before rotation; this offline handoff does not verify them.

After successful operator confirmation, the orchestrator can update A-03. Never
restore a retired key as rollback; repair the consumer configuration or issue a new
key through the same operator process. This documentation can be reverted independently.
It belongs on default `main` independently of homepage work.
