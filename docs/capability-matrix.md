# Capability Matrix

The checked source of truth is [`capability-matrix.yaml`](capability-matrix.yaml). It records the
shared service primitive, CLI command, API route, UI consumer where one exists, durable effect,
actor policy, and acceptance evidence for every current FD-7 row. CI rejects an `implemented` row
whose declared surface or evidence disappears. Missing breadth remains visibly `planned` with an
owning completion unit.

## Actor policies

- `authenticated-read`: the adapter verifies the principal; matter-scoped API reads are audited.
- `authenticated-human`: CLI derives the effective OS user and API derives the Access principal.
  Callers cannot submit the actor identity.
- `hard-human`: an agent may prepare the artifact, but cannot record the approval. Task locks,
  matter closure, OAuth consent, failover authorization, and substantive curation stay here.
- `policy-delegable`: automation is allowed only inside a recorded matter policy and bounded cap.

The trusted adapter must derive `actor`, `channel`, and time. Domain services accept those values so
CLI and API can share one primitive; public request bodies must never accept them. Every mutating
matter API route also requires authentication, CSRF, and an access-audit dependency.

## Completion rule

`implemented` means the named service, CLI, and API surfaces exist and the evidence files exercise
their contracts. A UI is required only when the row names one; it must consume the API rather than
reimplement domain logic. `planned` means the missing surface is still queued—never that a hidden or
manual workflow should be treated as complete.
