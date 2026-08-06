# AUDIT — what the blind personas actually produced (2026-08-05)

**Verdict in one line: this machine holds no production run artifacts, so the question
"is prior work product contaminated?" CANNOT be answered from here — it can only be
answered on the hosted box, and § 6 says exactly which two commands would answer it.**
What this machine *does* establish, from durable repo evidence, is the window (every
headless turn ever executed), and one finding that cuts against the alarming reading: no
persona was ever instructed to read the vault.

Prompted by the defect fixed in `467e812` / `0291062` / `cffcae9`. Read
`src/mootloop/engine/claude_provider.py` (the three docstrings) for the mechanism; this
file is only about the blast radius.

---

## 1. The defect, stated once

`build_settings` emitted `Read(/**)` in `permissions.deny` and `Read(<vault>/**)` in
`permissions.allow`. Claude Code evaluates deny before allow and never re-opens a denied
path, so the allow was dead; and `Read(/**)` gates *path* read access rather than the tool
named `Read`, so `Glob` and `Grep` were refused too. A persona turn had **no filesystem
access of any kind**. It exited 0 with a terminal `is_error: false`, so nothing upstream
saw a failure.

**Window: every headless turn ever run.** `git log -S'Read(/**)' -- src/mootloop/engine/claude_provider.py`
returns exactly one commit — `e354327`, 2026-07-12, the commit that introduced the
provider. The rule was present from the provider's first line until today's fix. There is
no "before" period to compare against.

---

## 2. What was examined, and what was found

**Nothing on this machine records a production run.** Counts, plainly:

| | count |
|---|---|
| production matter vaults found locally | **0** |
| production runs examined | **0** |
| production turns examined | **0** |
| ephemeral test runs found (excluded, see below) | 4 runs / 44 turn files |
| distinct matter ids ever seeded on this box | 8, all synthetic |

Where I looked (absence claims carry their commands):

- `find / -maxdepth 6 -name matter.yaml` (excluding `/proc`, `/sys`) → the repo fixture
  `fixtures/synthetic-matter/matter.yaml` and pytest temp directories, nothing else.
- `find / -maxdepth 8 \( -name journal.jsonl -o -name run.json -o -name turns -type d \)`
  → only `/tmp/tmp*/matters/acme-v-widgets/runs/drive-0001/`.
- Deeper, unbounded sweeps of `~/Coding Projects`, `~/worktrees`, `~/.local/state`,
  `~/Documents`, `~/Desktop` for `journal.jsonl` and `turns/` → nothing.
- `find /home /srv /opt /var/lib -name .canary` → only Docker image layers
  (`.../overlayfs/snapshots/*/fs/app/demo-vault/`), i.e. the demo vault baked into an
  image, not a run.
- `/srv/mootloop-matters` (the registry default) **does not exist on this machine**:
  `ls -d /srv/mootloop-matters` → "No such file or directory".
- `~/.mootloop/` holds exactly two files: `canaries.json` and `secrets.env`.

**The four `/tmp` runs are test output and are excluded.** They are
`acme-v-widgets/runs/drive-0001`, created 2026-08-05 09:59 by that morning's `pytest`.
Their producer is `FakeLLMProvider` (`tests/unit/test_engine_worker.py:33-34`), so no
`claude` subprocess and no sandbox were involved. Their turn bodies are the synthetic
fixture's ROG text.

**The canary registry is a census of every vault ever created here, and it is entirely
synthetic.** `create_vault` seeds a canary at creation (`vault.py:306`), so each distinct
matter id in `~/.mootloop/canaries.json` is a vault that once existed. It holds 4,513
canaries across 8 ids: `acme-v-widgets` (3,245), `northfield-widgets-v-granite-supply`
(810), `alpha` (132), `m1` (123), `bravo` (69), `charlie` (69), `delta-v-echo` (63),
`acme` (2). Every one is a test or fixture name. **No id matching the client convention
`YYYY-MM-DD-<client>-<descriptor>` has ever been seeded on this machine** — no real matter
was ever created here. (Caveat: the hosted tier overrides the registry with
`MOOTLOOP_CANARY_REGISTRY`, so this census speaks only for this box.)

---

## 3. The real runs happened, and they are on the hosted box

This is not a case of "the defect never mattered because nothing ran". Live runs did
happen — off this machine. From `docs/handoffs/2026-07-15-RESUME.md` (Addendum 2) and the
commit record:

- Matter `2025-10-16-riehl-fence` was seeded **on the hosted box**, 317 files ingested
  (309 unique), served set parsed to 54 discovery units.
- **Run 1** died at turn 0: valid `DraftOutput` JSON wrapped in a markdown fence, discarded
  3× as schema-invalid, zero completed turns. Fixed by `5c8fbce` (`_unfence`).
- **Run 2** (`discovery-responses-202607151943454876150000`): **3 turns completed**, the
  associate draft passing the degeneracy, completeness and fabrication gates; ~158K tokens
  metered; then a redraft discarded 3× on `hedge_subject_to` → `needs_attention` with 4
  open decisions.
- A re-kick followed on 2026-07-16 (`050cf30`, `6e83ed9`, `3c7f991`, `bd02406` are all
  prompt/persona fixes derived from live behavior).

Those artifacts live at `/srv/mootloop-matters` on the hosted box. **I did not touch it**
— out of scope for this audit by instruction, and it is the machine holding privileged
client material.

**Run 1 alone refutes the comfortable hypothesis.** The diagnostic that found this defect
guessed (at "moderate" confidence) that blind turns would fail loudly — that personas
would refuse and ask to be handed file contents, producing unparseable JSON rather than
plausible work product. Run 1 is a counter-example from production: a blind persona
returned **valid `DraftOutput` JSON**. It was discarded for its markdown fence, not for
its content. Blind personas draft; they do not necessarily complain.

---

## 4. The finding that cuts the other way: nobody ever told a persona to read the vault

`grep -niE "vault|read the file|source-docs|exhibit|Read\(|Glob|Grep|open the" personas/*.md`
across all seven persona bodies returns **zero matches**. `render_prompt`
(`stages.py:652`) assembles persona body + a fenced DATA block + the output contract; the
DATA block carries `request_id`, `request_text`, prior-turn output and retry feedback —
all injected by the deterministic core, none of it read by the persona.

So the vault grant was defense-in-depth around a capability the prompts never invoke. The
personas were not deprived of an input they had been told to fetch; their designed input
channel is the DATA block, and that channel was never affected by this defect.

That materially narrows the exposure. It does not eliminate it: the tools were in
`--allowedTools`, and a model that decides on its own to consult a file it can see
referenced in the DATA block would have been refused silently. Whether that ever happened
is a question about the hosted artifacts, not about this repo.

---

## 5. Verdict

**Can prior work product be trusted? Not determinable from this machine — and the answer
is probably "yes on this axis, no on another".**

Stated by confidence:

- **Established.** Every headless turn from 2026-07-12 to 2026-08-05 ran with zero
  filesystem access, and any refused tool call was invisible to the run journal.
- **Established.** No persona prompt ever directed a persona to open a vault document, so
  the defect did not silently remove a designed input.
- **Established.** No production run artifact exists on this machine to audit. Every
  count in § 2 that matters is zero.
- **Cannot determine from available artifacts.** Whether any hosted turn attempted a read,
  and whether any turn cited a vault document it could not have opened. § 6 settles it.
- **Worth Damien's attention independent of this defect.** FE-7 Run 2's completed
  associate draft passed the fabrication and completeness gates while, in the handoff's
  own words, "zero client facts are loaded, deliberately". A draft built from no facts and
  no documents is not contaminated by *this* bug — it is a filing with nothing under it.
  Any prior deliverable should be judged on that basis, which is the same conclusion the
  run itself reached when it stopped at `needs_attention`.

**Nothing has been exported to a court.** The export path is gated (`12f455b`, `e1e57b2`,
`4e57ae0`) and both known runs ended `needs_attention`.

---

## 6. What would settle the open question

Two commands on the hosted box (`/srv/mootloop-matters/2025-10-16-riehl-fence/runs/`),
run by someone with authority to read that matter:

1. **Did any turn cite a document it could not have read?** Search the turn bodies for
   citations to ingested filenames or `source-docs` paths. A persona provably unable to
   open a file cannot honestly cite it, so any hit is direct evidence of fabrication and
   the corresponding run must be discarded rather than reviewed.
2. **Did any turn attempt a read at all?** The old provider kept no per-tool record, so
   this is only answerable from CLI-side logs if the container retained them. If it did
   not, the honest answer stays "unknown", and the run should be re-driven on the fixed
   provider rather than trusted.

A third, cheaper option makes both moot: **re-drive the fence run on the fixed provider.**
Turns are replayable, both known runs ended `needs_attention` with open decisions anyway,
and a turn refused by the sandbox now fails loudly instead of returning an apology as work
product.

---

## 7. Privacy note

No substantive legal content was read in the making of this audit. Vault *locations* were
searched by filename; matter identifiers were read from the canary registry keys and from
the repo's own handoff; no canary token value, no credential, and no client prose appears
here or was copied anywhere. The one production matter named (`2025-10-16-riehl-fence`,
Ramsey County 62-CV-26-2379) is already named in `docs/handoffs/2026-07-15-RESUME.md` and
is Damien's own matter.
