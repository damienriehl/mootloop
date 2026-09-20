# Explore and replay the demos locally

The public library contains five fictional litigation matters, five public-record
counterfactuals and ten fictional business questions. Each is a prepared script
replayed through MootLoop's task planner. The hosted viewer accepts no documents,
keys or execution requests. No live model calls are needed to replay an example.

## Install

Install Python 3.12 and [uv](https://docs.astral.sh/uv/), then:

```bash
git clone https://github.com/damienriehl/mootloop.git
cd mootloop
uv sync
uv run mootloop --help
```

Open [the library](https://mootloop.org/demos/), choose an example, and download its
revision-specific input bundle under **Use locally**. Save it outside the checkout.
The bundle records the preparation software revision and exact input hashes.

## Replay an example

Every detail page supplies commands for its actual task, document-input IDs and
strategies. For the supplier-termination business example, using a new external
vault and a downloaded bundle saved as `~/Downloads/inputs.json`:

```bash
uv run mootloop web import-demo "$HOME/Downloads/inputs.json" \
  "$HOME/MootLoopDemos/supplier" --matter-id 2026-09-20-supplier-demo
uv run mootloop web replay-script "$HOME/Downloads/inputs.json" reviewed-advice \
  "$HOME/MootLoopDemos/supplier-replay.json"
uv run mootloop run start "$HOME/MootLoopDemos/supplier" --task business-advice \
  --run-id reviewed-advice --document-input advice --mode autonomous
uv run mootloop run drive "$HOME/MootLoopDemos/supplier" reviewed-advice \
  --replay "$HOME/MootLoopDemos/supplier-replay.json"
uv run mootloop run status "$HOME/MootLoopDemos/supplier" reviewed-advice
```

Use a new destination for imports. The importer creates fresh operational identity
and a new canary; it does not copy approvals, seals, journals, credentials or run
state. Keep vaults outside Git repositories and background-sync folders. Replays
fail if their bound input bytes change. Discovery and document tasks have different
inputs; use the commands attached to the selected example rather than substituting
a task name into another demo's command.

For real cases, run the two supplied strategies separately. Their selected inputs
share only eligible public summaries. Later outcomes appear in the public snapshot,
not in the historical drafting inputs. The Tesla monetary-remedy alternative
explicitly assumes different earlier preservation and evidence development.

## Exercise the manual workflow

Use a new run with the same explicit document selection. Instead of `run drive
--replay`, use `run plan-next`, `run prompt` and `run record-turn` to inspect a
scheduled turn and record schema-valid output. `run pause`, `run continue` and
`run status` expose the lifecycle. Consult `uv run mootloop run --help` and each
subcommand's help for arguments. This route can produce different work product;
it is not the prepared replay and does not inherit its editorial review.

Document workflows assemble draft Markdown review copies. The examples retain
unresolved factual-support, citation, decision and attorney-attestation gates;
some also retain failed substantive rubrics. A completed scripted run is not a
clean-export authorization or a prediction of litigation success. The original
`/legacy` example separately demonstrates simulated approval mechanics.

## Run the read-only viewer locally

Download the archive identified by `config/demos/release.json` to an external
folder, preserving the filename `release.tar`. Its SHA-256 pin is authoritative.
For example, after saving it to `/tmp/mootloop-demo-build-context/release.tar`:

```bash
uv sync --extra web
uv run python tools/validate_demo_release.py \
  /tmp/mootloop-demo-build-context/release.tar \
  --pin config/demos/release.json --stage /tmp/mootloop-public
MOOTLOOP_PUBLIC_ROOT=/tmp/mootloop-public \
  uv run uvicorn mootloop.web.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/demos/`. `/legacy` serves the original fictional discovery
example. A missing or corrupt projection fails closed; the server never falls back
to a local matter vault. The archive contains reviewed public projections and
permitted local inputs, not the private filings' wholesale source PDFs.
