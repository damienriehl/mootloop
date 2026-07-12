# Using MootLoop for a live matter

The complete local workflow: from an organized case folder to attested,
court-formatted discovery responses — driven from Claude Code on a Claude Max
plan.

> **Where things run.** A live matter runs **entirely on your machine**. Fable
> (Claude Code's orchestrator model) drives the pipeline; each persona turn
> (Associate, Partner, OC, Judge panel, Rubric Judge) runs as a Claude Code
> subagent on your **Max plan quota — zero API cost**. The dollar figures the
> CLI shows (`run estimate`, `run status`) are **notional** plan-quota
> accounting, not billed spend. The only credential you need is a **free**
> CourtListener token for citation verification.
>
> **The DEV/PROD servers are demo-only.** `mootloop.dev.openlegalstandard.org`
> and `mootloop.org` serve a pre-baked synthetic demonstration — **never put
> matter data on them.** Matter vaults never leave your machine.

## 0. One-time setup

```bash
git clone https://github.com/damienriehl/mootloop && cd mootloop
make setup          # uv sync + pre-commit install (needs Python 3.12 + uv)
```

Secrets live **only** in `~/.mootloop/secrets.env` (or the OS keychain) — never
in `matter.yaml`, task config, or the vault:

```bash
mkdir -p ~/.mootloop
cat > ~/.mootloop/secrets.env <<'EOF'
# Free account at https://www.courtlistener.com (Profile -> API token).
COURTLISTENER_TOKEN=<your-40-char-token>
EOF
chmod 600 ~/.mootloop/secrets.env
```

For DOCX deliverables, install pandoc locally (`apt install pandoc` /
`brew install pandoc`). Markdown deliverables need nothing extra.

## 1. Initialize the matter vault — OUTSIDE any repo

Vaults hold matter data, so they must live outside every git tree and outside
background-sync folders (Dropbox/Drive/iCloud — preflight blocks them):

```bash
uv run mootloop init ~/Matters/<matter-id> \
    --matter-id <matter-id> \
    --court "District Court, <County> County" \
    --case-number "<file-no>" \
    --our-side defendant \
    --jurisdiction-state MN \
    --forum state

uv run mootloop validate ~/Matters/<matter-id>
```

(Or write a full `matter.yaml` — parties, attorney block, gates, budget tier,
deadlines — and pass `--from-yaml`; see `fixtures/synthetic-matter/matter.yaml`
for the shape.)

## 2. Ingest the organized case folder

Organize the case documents in a folder, then describe each file's role and
privilege in a `tags.yaml` (glob → tags; later rules win per field):

```yaml
# tags.yaml
"complaint.pdf":   {role: complaint, privileged: false}
"answer.docx":     {role: answer, privileged: false}
"contract*.pdf":   {role: client-doc, privileged: false}
"*.eml":           {role: correspondence, privileged: false}
"counsel-memo*":   {role: correspondence, privileged: true}
```

```bash
uv run mootloop ingest ~/Matters/<matter-id> ~/case-docs --tags ~/case-docs/tags.yaml
```

Anything unreadable or needing conversion is reported, never silently skipped.

## 3. Parse each served discovery set

One parse per served document (`rog` | `rfp` | `rfa`):

```bash
uv run mootloop requests parse ~/Matters/<matter-id> served/rogs-set1.txt --type rog --set 1
uv run mootloop requests parse ~/Matters/<matter-id> served/rfps-set1.txt --type rfp --set 1
uv run mootloop requests parse ~/Matters/<matter-id> served/rfas-set1.txt --type rfa --set 1
```

## 4. Load client facts (with provenance)

Every factual assertion in a response must trace to a fact or the corpus
(fabrication gate). Add facts from a JSON file whose provenance quotes real
passages in ingested documents:

```bash
uv run mootloop facts add ~/Matters/<matter-id> --input facts.json
uv run mootloop facts list ~/Matters/<matter-id>
```

## 5. Drive the run from Claude Code (Max plan — no API cost)

Open Claude Code in the repo and invoke the run skill:

```
/moot-run ~/Matters/<matter-id> --mode gated
```

Fable orchestrates the stepwise core (`plan-next` → spawn persona subagent →
`record-turn`); persona turns execute as Claude Code subagents on plan quota.
`--mode gated` pauses at stage boundaries (`mootloop run continue` resumes);
`observed` streams `runs/<run-id>/STATUS.md`. Check anytime:

```bash
uv run mootloop run status ~/Matters/<matter-id> <run-id>
uv run mootloop run panels ~/Matters/<matter-id> <run-id>   # objection survival
```

Budget: `uv run mootloop run estimate` before, `run status` during — both
notional (plan mode). A `budget.hard_cap_usd` in `matter.yaml` checkpoints the
run gracefully at the cap; `run raise-cap` reopens it.

## 6. Resolve the attorney gates

The run cannot finish past open hard-human decisions (privilege calls, RFA
dispositions), and nothing exports while ANY decision is open:

```bash
uv run mootloop decide list ~/Matters/<matter-id> <run-id>
uv run mootloop decide show ~/Matters/<matter-id> <run-id> <decision-id>
uv run mootloop decide resolve ~/Matters/<matter-id> <run-id> <decision-id> \
    --action approve --by "Your Name"
```

`--action modify --choose <option-key>` picks a different option; `deny`
rejects the proposal.

## 7. Verify citations, then attest

```bash
uv run mootloop cite verify ~/Matters/<matter-id> --run <run-id>
uv run mootloop research list ~/Matters/<matter-id>    # anything needing human research
uv run mootloop run gates ~/Matters/<matter-id> <run-id>   # the export predicate
uv run mootloop attest ~/Matters/<matter-id> <run-id> --by "Your Name"
```

Attestation hashes the master; any later edit re-imposes DRAFT. The citation
check carries a standing disclosure: currency is **not** checked against a
citator — confirm good-law status yourself.

## 8. Export

```bash
uv run mootloop export build ~/Matters/<matter-id> <run-id>
```

Markdown deliverables always build (master, per-set masters, verification
page, privilege log, strategy memo, audit log). DOCX renders per served set
when pandoc is installed — **DRAFT-watermarked** unless the run is attested
AND the gate ledger is green AND the residue scan passes. Or drive it from
Claude Code: `/moot-export ~/Matters/<matter-id> <run-id>`.

## Rules that never bend

- **Matter data never enters this repo** — vaults live outside every git tree.
- **Servers are demo-only** — never upload, sync, or bake a real matter into
  any deployed environment.
- **Secrets only in `~/.mootloop/secrets.env`** or the OS keychain.
- **You are the attorney.** Every gate exists so a human owns the judgment
  calls; the attestation is your professional signature over the work.
