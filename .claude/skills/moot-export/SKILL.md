---
name: moot-export
description: Build a MootLoop run's court-formatted deliverables (master, verification page, privilege log, strategy memo, audit log) and render DOCX per set — draft until attested, clean only when the gate ledger is export-ready and the residue scan passes.
disable-model-invocation: true
argument-hint: <vault-path> <run-id> [--force-draft]
---

# moot-export

You are the **export driver** for a completed MootLoop run. The shared export core
(Python) builds every deliverable and enforces the watermark/attestation/residue
rules — a raw call cannot produce an un-attested clean export (plan D3 M12). You only
run the preflight gate check, invoke export, and surface blockers to the attorney.

`$VAULT` = first argument. `$RUN` = second argument.

## 1. Preflight — is the run export-ready?

```
uv run mootloop run gates "$VAULT" "$RUN"
```

- `export_ready: True` → a clean export is possible; go to step 2.
- `export_ready: False` → note the **blockers** (open decisions, missing/invalidated
  attestation, unverified citations, a failed fabrication/rubric gate). Surface them
  to the attorney. A draft export is still valuable; continue to step 2 (the DOCX will
  carry the DRAFT watermark).

Do **not** resolve gates or attest yourself — privilege calls, RFA dispositions, and
attestation are human-by-design (`decide`/`attest` are their own verbs).

## 2. Export

```
uv run mootloop export build "$VAULT" "$RUN" [--force-draft]
```

This always writes the markdown deliverables under `deliverables/<run-id>/`:
`master.md`, `verification.md` (rog sets only), `privilege-log.md`,
`strategy-memo.md`, `audit-log.json`, and per-set masters under `sets/`. It renders a
DOCX per served set under `docx/` — clean only when the run is attested AND the gate
ledger is green AND the residue scan passes; otherwise `.DRAFT.docx` with the
DRAFT-watermark template.

If pandoc is not installed the DOCX step is skipped with a clear notice; the markdown
deliverables are still produced.

## 3. Surface the result

- List what was produced (paths printed by the command).
- If `export_ready: False` or `draft=True`, tell the attorney the copy is a DRAFT and
  name the blockers. The path to a clean copy is: resolve open decisions
  (`decide list`/`decide resolve`), verify citations (`cite verify --run`), then
  `attest`, then re-run export.
- If any `residue FAIL` line appears, the DOCX carried annotation residue — report it;
  the clean file was withheld.

## Rules

- Never hand-edit a deliverable to clear a gate. The gate ledger is the single source
  of truth; export reads it, never overrides it.
- The verification page is unsigned — the client signs the perjury declaration on
  paper. Never fabricate a signature.
- Everything under `deliverables/` is matter data. It never leaves the vault except by
  the attorney's explicit action.
