# Gate-ledger repeated-work remediation

## Result

The deferred performance finding from the adversarial review is repaired locally.
The ordinary 18-request synthetic case's warm ledger median fell from **465.88 ms
to 227.61 ms (51.1%)** in a paired comparison. No export policy, concurrency,
cross-read caching, stage isolation, or privileged-disposition behavior changed.
The user authorized autonomous continuation after the measurement report.

`gate_ledger.build_ledger` now loads the context once, reads and folds one journal
snapshot, and selects each request's operative draft once. Both gate ownership
and decision-revision checking use that selection. The new
`orchestrator.operative_draft_records` helper retains `StageContext`'s defensive
copies. Existing public operative-ID and draft APIs delegate to the same helper.
No mutable context/state is retained across reads.

## Paired evidence

`tools/compare_gate_ledger.py` loads the baseline ledger from local commit
`73833b4613ebdab9941044ec8aa13e39b81bb1d0` using `git show`, without checkout or
network. Baseline and candidate use the current public orchestrator wrappers;
those wrappers preserve the existing selection behavior and defensive copying.
The baseline therefore still performs two selections and three folds. This is
an isolated ledger-path comparison, not a deployment-to-deployment benchmark.

The harness uses the same five disposable synthetic cases as the
[measurement audit](2026-09-20-gate-ledger-measurement.md). Each case has 15 reads
per implementation per cache mode, with implementation and mode order alternated.
Every one of the **300 timed reads** equals the baseline ledger document. All five
vault-content fingerprints are unchanged. Untimed warm-ups and profile calls are
additional to those 300 reads. The JSON preserves individual timings:
`2026-09-20-gate-ledger-comparison.json`.

| Case | Warm baseline ms | Warm candidate ms | Reduction | Parser-cold baseline ms | Parser-cold candidate ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| One request, 12 turns | 4.74 | 1.79 | 62.2% | 4.89 | 2.08 |
| 18 requests, no turns or decisions | 10.35 | 4.68 | 54.7% | 10.37 | 4.52 |
| 18 requests, 216 turns | 465.88 | 227.61 | 51.1% | 479.46 | 240.88 |
| Same turns, 5x journal events | 491.27 | 236.96 | 51.8% | 503.94 | 265.94 |
| Same turns, 20x journal events | 686.48 | 339.68 | 50.5% | 746.51 | 416.96 |

These are medians; reduction uses unrounded median values. Parser-cold clears only
the existing journal parser cache. History multipliers add repeated gate events,
not requests, provider calls, spend, or completed turns. Cases ran on a shared
machine alongside the test suite, so absolute times are not comparable to the
earlier unpaired measurements and are not production latency guarantees.

Profiles confirm the structural improvement in every case: **three folds become
one; three context loads become one; two request traversals become one**. For
18 requests, this reduces `StageContext` construction from 36 to 18. The remaining
single traversal still defensively copies the state for every request; this work
does not claim to remove all scaling cost.

## Correctness and review

The new one-traversal regression failed against the old code (`2 != 1`) and passes
after the repair. Additional regressions cover an unfinished run with no draft,
fresh events appearing after an earlier read, selected-record mutation isolation,
and complete journal corruption after a successful read. The existing fabrication
tests cover cured/superseded and still-failing operative drafts. The focused
ledger/reviewed-output suite passed **29 tests in 0.92 seconds**.

Local diff review checked that selection order, missing drafts, draft validation,
per-turn gate precedence, worst historical gates, decision revision requirements,
and attestation checks retain their existing behavior. The journal snapshot is
shared only within one ledger read; no whole-vault transactional snapshot or new
concurrent-writer guarantee is claimed. The paired benchmark checks equivalence
on static fixtures; regression and full-suite tests supply broader correctness
coverage. No external or independent-agent review was performed.

Final **`make check` passed** in the isolated environment: Ruff, strict mypy
across 135 source files, and **1,315 tests in 464.46 seconds**, with one paid test
deselected, one dependency warning, no skips, and 91% coverage. The final
measurement-tool edits also passed a repository-wide Ruff check and targeted
format check. The local branch is `fix/gate-ledger-single-pass`; the focused
commit includes this fix and both the baseline and paired measurement evidence.
Unrelated `.claude/RESUME.md` and the older untracked handoff are excluded.

The reusable original measurement tool now recognizes either operative-selection
API and labels selection timings generically in schema 1.1. Its updated path
passed a separate 30-read synthetic smoke check. Original schema 1.0 baseline
numbers and harness hash remain preserved as historical evidence.

## Reproduction and limits

Use the isolation command in the measurement audit, replacing
`tools.measure_gate_ledger` with `tools.compare_gate_ledger` and redirecting stdout
to a different result path. Real home stays hidden, environment is cleared,
network is disabled, and all synthetic vaults live in the namespace's private
`/tmp`. The harness requires the baseline commit to exist locally. No real vaults,
credential files, or paid providers are accessed.

This closes the measured duplicate-work P2, not D-10 or any product/domain
decision. U-12/U-13 shipping, privilege disposition design, concurrency, and
caching remain outside this change. No push, merge, deployment, or migration is
part of the work.
