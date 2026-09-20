# Gate-ledger read cost — synthetic measurement

This is the pre-refactor measurement record. The subsequent authorized fix and
paired comparison are recorded in
[the remediation audit](2026-09-20-gate-ledger-remediation.md).

## Scope

This follows the deferred P2 in `2026-09-19-adversarial-remediation.md` after the
user authorized the recommended synthetic measurement. Production source remains
unchanged at repair commit `73833b4` (current HEAD `ceb99d0` adds the handoff).
No concurrency, caching, export policy, D-10, or privilege-workflow decision is
made here. No real vault, credential file, network, or paid provider was used.

The reproducible harness is `tools/measure_gate_ledger.py`; raw samples and
profiles are in `2026-09-20-gate-ledger-measurements.json` beside this document.
Only aggregate numbers and function names are retained, never generated matter
text. The harness creates its own fixtures in a private temporary directory and
accepts no existing vault path.

## Method

- Use the existing single-request unit fixture and 18-request integration fixture
  with `FakeLLMProvider`. Measure the latter both before any turns and after the
  fake run stops for decisions. No decisions are resolved or attestation made.
- Extend the 18-request journal to 5 and 20 times its original event count using
  repeats of one valid gate evaluation. These are artificial history-length
  stress cases, not additional completed turns, requests, or realistic matters.
- Take 15 measurements per mode per case, alternating mode order. Warm mode
  primes the ledger with one untimed read. Parser-cold mode clears the existing
  journal parser cache immediately before each read; it does **not** clear the
  operating-system filesystem cache or restart Python.
- Time the real `operative_drafts` call inside the real ledger call using a
  delegating wrapper. Nothing is stubbed or omitted. Inclusive time includes
  context loading, state folding, request traversal, defensive copies, and draft
  validation. It is not the cost of the third fold alone and is not a demonstrated
  achievable speedup. Total timing includes `to_dict()` and wrapper overhead.
- Separately profile one warm ledger call per case with cProfile. Profile times
  are instrumented and must not be compared directly to wall-clock samples or
  summed across nested functions. Counts establish call multiplicity; timings
  locate cost. No production latency target or threshold was approved.
- Assert identical ledger output across every timed read. Hash every synthetic
  vault file before and after the measurement, including profiling, and assert
  unchanged file contents and file membership. This does not test concurrent
  writers or filesystem metadata invariance.

## Results and interpretation

All times below are medians of 15 samples. Paired share is the median of each
sample's added-call/total ratio, so it need not equal the ratio of the two medians.

| Case | Requests / turns | Events | Warm ledger ms | Added call ms | Paired share | Parser-cold ledger ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| single_request | 1 / 12 | 49 | 4.81 | 1.76 | 35.9% | 5.12 |
| eighteen_requests_unfinished_no_decisions | 18 / 0 | 1 | 10.58 | 4.24 | 40.4% | 10.64 |
| eighteen_requests | 18 / 216 | 746 | 431.94 | 213.39 | 47.3% | 444.92 |
| gate_history_5x | 18 / 216 | 3,730 | 505.37 | 243.35 | 50.5% | 524.11 |
| gate_history_20x | 18 / 216 | 14,920 | 525.80 | 239.30 | 45.2% | 596.37 |

The added operative-draft call is a material part of the measured ledger read:
**213.39 ms of a 431.94 ms warm median** in the ordinary 18-request fixture.
The no-decision case still traverses all 18 requests twice, taking 4.24 ms inside
the added call even with no completed turns. The single-request completed fixture
has one decision; the 18-request completed fixture and both history stress cases
have eight. Every measured case remains non-exportable.

Every profiled ledger read makes **three `load_state`/`fold` calls, three
`load_run_context` calls, and seven `read_events` calls**. The 18-request cases
construct **36 `StageContext` objects**, once per request in each of the two
operative-draft lookup functions.

The significant attribution is repeated defensive copying. In the ordinary
18-request profile, `_context_for` accumulates 1,718.56 ms, while all three journal
folds together accumulate 5.61 ms (instrumented times). The same profile records
1,387,236 total recursive `deepcopy` calls. Source inspection explains this:
`StageContext.__post_init__` in `src/mootloop/stages.py` deep-copies the full run
state, pipeline, and other inputs for each request to enforce mutation isolation.
`operative_draft_turn_ids` and `operative_drafts` in `src/mootloop/orchestrator.py`
each reconstruct those contexts. The concern is therefore broader than an extra
journal fold; removing only that fold would leave the dominant duplicate work.

At 20 times the event count, warm ledger median grows to 525.80 ms and parser-cold
median to 596.37 ms. The number of completed turns and their payload sizes remain
constant, so these data do not estimate scaling to 20 times as many requests or
completed turns. Cases were measured sequentially on a shared machine without
CPU isolation; small differences and nonmonotonic component timings are noise,
not evidence of improved scaling. A preliminary run of the four original cases
showed the same attribution (408.29 ms warm total and 209.48 ms added call for the
ordinary 18-request case); the persisted samples are the final five-case run.

**Recommendation:** retain the P2 as measured and unresolved. A later scoped
experiment should derive operative IDs and draft objects together within one
ledger read, using one loaded context/state and one per-request traversal, while
preserving defensive-copy and fail-closed semantics. Validate superseded gates,
revision requirements, missing drafts, malformed journals, and read-only behavior
before considering that implementation for adoption. Do not remove stage input
isolation globally or introduce a cross-read cache to obtain this saving.

This work establishes local read cost and its source, not a production SLA breach,
user-perceived delay, UI/network latency, concurrency limit, or measured speedup.
It does not satisfy D-10 or authorize caching/concurrency work under U-16.

## Isolation and reproduction

Run from the repository root on the same Linux machine with installed dependencies.
The command below reconstructs the isolation used for this measurement; it does
not depend on the disposable `/tmp/mootloop-safe-run.sh` wrapper. Machine-local
interpreter and executable paths may need adjustment on another machine.
Bubblewrap namespace creation required an approved outer-sandbox escalation.

```bash
/usr/bin/bwrap \
  --ro-bind / / \
  --tmpfs /home/damienriehl \
  --ro-bind /home/damienriehl/.local/share/uv/python /home/damienriehl/.local/share/uv/python \
  --ro-bind /home/damienriehl/.local/bin/uv /home/damienriehl/.local/bin/uv \
  --ro-bind /home/damienriehl/.local/bin/pandoc /home/damienriehl/.local/bin/pandoc \
  --ro-bind /home/damienriehl/.nvm/versions/node/v24.13.0 /home/damienriehl/.nvm/versions/node/v24.13.0 \
  --bind "$PWD" "$PWD" \
  --tmpfs /tmp --proc /proc --dev /dev --unshare-net --die-with-parent \
  --clearenv \
  --setenv PATH /home/damienriehl/.nvm/versions/node/v24.13.0/bin:/home/damienriehl/.local/bin:/usr/local/bin:/usr/bin:/bin \
  --setenv UV_NO_SYNC 1 --setenv UV_OFFLINE 1 --setenv UV_CACHE_DIR /tmp/uv-cache \
  --setenv PYTHONDONTWRITEBYTECODE 1 --setenv COVERAGE_CORE sysmon \
  --chdir "$PWD" uv run python -m tools.measure_gate_ledger \
  > /tmp/mootloop-ledger-measurements.json
```

The namespace hides the real home, clears inherited environment variables, gives
the synthetic vault a private `/tmp`, and disables network access. Only the repo
and installed tool paths are rebound from the hidden home. Do not generalize this
recipe to untrusted repositories or assume it hides arbitrary data outside home.

## Verification and disposition

The final harness passes Ruff lint and format checks. The existing gate-ledger
correctness tests pass: **2 passed in 0.47 seconds**, inside the same isolation.
The harness assertions verify stable output and unchanged vault file contents.
No production source changed, so the full suite was not rerun. No commit, push,
merge, deployment, or migration was performed. Unrelated resume/handoff changes
were preserved.
