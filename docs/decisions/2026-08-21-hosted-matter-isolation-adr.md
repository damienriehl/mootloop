# ADR: per-matter hosted workers behind an authenticated egress proxy

- Status: accepted for local implementation proof
- Date: 2026-08-21
- Decision gate: deployed proof remains blocked on D-03

## Context

The original hosted driver mounted the complete matters root into one container under
one UID. Its provider accepted an optional caller-supplied wrapper, and the compose
network could reach arbitrary Internet destinations. Claude's tool-deny settings were
useful defense in depth, but they did not turn a shared OS mount and unrestricted
network namespace into matter isolation.

## Adversarial spike

Three candidate boundaries were tested against two hostile actions: opening a sibling
matter path and connecting to a non-model destination.

| Candidate | Sibling path | Arbitrary egress | Result |
|---|---|---|---|
| Shared root, shared UID, provider permission rules | Mounted and OS-readable by the worker | Container network is unrestricted | Reject |
| Shared container with per-matter Unix identities and path ACLs | Depends on flawless host ACL/UID provisioning; root/container compromise spans matters | Still needs a network broker | Reject |
| One worker container per matter, one matter bind mount, internal network to an authenticated allowlisting proxy | Sibling vault is absent from the mount namespace | Worker has no external network; Squid permits authenticated CONNECT only to the fixed model host | Accept |

The accepted topology is the least complex option that makes both denials structural.
It needs no Docker socket, root daemon, per-matter Linux account database, or dynamic
ACL mutation inside the application.

## Decision

Each hosted worker binds exactly one validated `matter_id`. Its container mounts that
matter, shared queue control metadata, the read-only global canary registry, and the
driver's read-only secrets directory. Queue claims are filtered to the
bound matter. Before exec, the provider wrapper applies an unprivileged Landlock
allowlist containing only immutable runtime files and the per-run Claude config tree.
The matter vault, shared `.queue`, global canary registry, and complete secrets mount
are inaccessible to the provider subprocess after the deterministic parent injects the
single required credential. Normal persona turns receive matter context through fenced
prompt DATA and have no shell/web/filesystem tools. Sibling vault contents are not
mounted at all.

Landlock works without setuid, `SYS_ADMIN`, an unconfined seccomp profile, or a Docker
socket. Unsupported kernels fail closed before model execution.

Hosted provider construction requires the exact built-in egress preflight wrapper,
launched through Python isolated-import mode so a vault cannot shadow its module.
Before any subprocess starts, it also requires the fixed `http://egress-proxy:3128`
endpoint and proxy password from the secrets store. The worker joins only an internal
network shared with its healthy proxy. The proxy alone joins an outbound bridge,
receives only a dedicated password-file secret (not the driver's secrets mount), and
requires Basic proxy authentication before allowing CONNECT to
`api.anthropic.com:443`; all other destinations and ports are denied. Local and dev
providers are explicit modes and do not receive proxy variables.

One outbound trust-conversion service recursively checks a payload for every
registered matter canary, denylisted literal, and exact registered/secrets-file value
before JSON serialization. Tripwires block. Secret-shaped text is redacted. Only then
does the service return `PublicText`. SSE and operator notification payloads use this
service; future notification and connector modules must do the same. Hosted mode also
requires a readable, regular, schema-valid registry; missing or poisoned policy state
blocks outbound data rather than silently disabling tripwires.

## Worker lifecycle

- **Create:** a host-side service validates the matter ID, registry-resolved vault,
  matter identity, vault preflight, compose file, and matching mode-0600 proxy secret,
  then derives the fixed bind source and starts a dedicated compose project containing
  one `driver` and one `egress-proxy`. No Docker socket is mounted.
- **Drain:** send normal SIGTERM. The worker finishes the in-flight provider turn,
  journals it, releases the queue claim at the next turn boundary, and exits within the
  630-second grace period.
- **Remove:** after the worker is stopped and has no claim, remove that compose project.
  Never remove the vault or shared queue as part of worker removal.
- **Recover:** a replacement project for the same matter reclaims expired/stale claims;
  journal folding resumes after the last durable turn. A missing proxy/auth secret or
  invalid mount keeps the replacement crash-stopped before model execution.

## Local evidence and residual gate

Unit and invariant tests plus a built-image probe prove bound queue selection, absence
of a sibling-root mount, Landlock denial of matter/control/secrets paths, exact
wrapper/proxy validation before subprocess creation, fixed proxy ACL/auth config, and
outbound tripwire behavior before JSON serialization. This is FD1-05 local proof, not
deployed evidence. Network-namespace, proxy startup, kill/drain, and planted
cross-matter probes on a real host belong to U-17A and require fresh D-03 authorization.
