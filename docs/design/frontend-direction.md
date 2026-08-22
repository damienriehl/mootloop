# Frontend Direction

The MootLoop cockpit should feel like a working case file, not a generic SaaS dashboard.
Hierarchy comes from the record: matter, run, stage, turn, gate, decision, and release. Use the
pleading spine for navigation, a bottom docket bar on mobile, serif type for argument, and mono
type for record metadata. Color carries legal state consistently: ink for ordinary record,
ochre for pending review, oxblood for a blocker, green only for verified completion, and pencil
gray for unavailable or derived material.

## Interaction contract

Every mutation must define entry/empty, queued/loading, partial/needs-attention, success,
stale/error, cancel, retry, and reopen behavior before implementation. Preserve user drafts on
error. Show durable job or run identity, safe progress, attempt history, and the next human gate.
Never imply that missing data is approved, silently retry a judgment, or optimistically display
attestation or release.

Keyboard and screen-reader operation are first-class. Maintain visible focus, status
announcements, semantic headings, 44-pixel targets, and a mobile order that preserves the
desktop decision hierarchy. React Flow may visualize a board, but the linearized ruled list is
the canonical accessible editing surface.

## Signature moments

- New turns and decisions ink into the timeline one at a time.
- Coverage seals distinguish proven, contested, gap, extrapolated, and unmapped board nodes.
- Begin Task uses one omnibox: empty shows suggestions, keywords search the catalog, and a
  sentence enters freeform resolution; all lanes converge on the same TaskSpec slip and lock.
- Binding RFA choices use a two-step “so ordered” ceremony.
- Downloads use a certify-and-release colophon showing run ID, rubric/context commitment,
  attestation state, export seal, and audit confirmation.

Avoid stat-tile grids, gavels, scales, parchment, force-directed hairballs, decorative purple
gradients, simultaneous animation, or color-only state. The interface earns trust by making
provenance, blockers, human authority, and recovery consequences legible.
