# Adversarial repair release

The release carries the existing-workflow safety repairs and the gate-ledger
performance repair on a branch based directly on `origin/main` at
`da339fffbd7828819184c6655c27d678a9809a2a`.

## Scope and authority

The source patches are `73833b4613ebdab9941044ec8aa13e39b81bb1d0` and
`60b863b55b0b5889aa0cf39c35e8bf529e6746ec`. They were applied without their
U-13 ancestors. The sole application conflict was the close-inventory exemption
set: the release retains the `CloseIntent` exemption and omits the board-model
exemptions. U-12/U-13 implementation and the U-13 `RunLock(create_parent)` extension
are absent.

The [owner's release decision](../decisions/2026-09-20-repair-release-and-privilege-boundary.md)
authorizes PR creation, merge, and deployment. It also records the future privilege
completion boundary. Current exports continue to fail closed when their review
requirements are unmet.

## Validation

Validation uses synthetic fixtures in a sandbox that hides the user's home,
disables networking, and does not expose credentials. No real-matter operation is
part of this release validation.

- Backend `make check`: 1,278 tests passed, one paid-oracle test deselected;
  Ruff passed and strict mypy passed across 133 source files. Coverage was 91%.
  One existing Starlette/httpx deprecation warning was emitted.
- Frontend `make -C frontend check`: 44 tests passed across 13 files; lint,
  typecheck, and schema synchronization passed.
- Fresh paired benchmark: all 300 reads across five cases and two cache modes
  matched baseline output and left vault bytes unchanged. No provider spend.

| Synthetic case | Baseline warm median | Release warm median |
| --- | ---: | ---: |
| Single request | 4.90 ms | 1.96 ms |
| Eighteen requests | 453.32 ms | 228.24 ms |
| Unfinished eighteen requests | 9.98 ms | 4.28 ms |
| Gate history, 5x | 462.57 ms | 232.64 ms |
| Gate history, 20x | 650.16 ms | 309.83 ms |

The earlier [measurement report](2026-09-20-gate-ledger-remediation.md) retains the
original branch's measurements. The table above is a fresh check on this separated
release tree, measured while other validation was running; it is not a production
latency guarantee.

## Review

The `ce-simplify-code` reuse, quality, and efficiency review found no justified
behavior-preserving simplification. Full `ce-code-review` validated one P2 finding:
explicit draft export must remain available when a sealed generation fails a newer
export gate. The fix permits only the explicit watermarked-copy path and reports the current
blockers. Its regression verifies unchanged sealed bytes and attestation records;
60 targeted tests passed. The final full `make check` passed with all four new test cases included.

Review receipt: `status: complete`, run `20260920-070322-323c9113`. All ten selected
lenses completed. The adversarial pass ran in-process because the cross-model
runner could not exclude unrelated dirty files. No source was sent externally.
The two privacy-test coverage gaps are covered by three added cases: direct
registry loads reject directories and symlinks, and conflicting canary registration
preserves the existing binding and registry bytes. All 56 targeted privacy tests passed.

The validator rejected the proposed legacy budget-settlement exception: the old
completion-before-spend sequence leaves genuinely unbooked spend after a crash.
Current replay can book that spend and clear the reservation; clearing on completion
alone would restore undercounting.

The benchmark comparison tool requires historical baseline commit `73833b4` to be
available locally. A fresh checkout of only this release cannot reproduce that
comparison without acquiring the baseline object. This does not affect runtime code.

## Deployment validation

After remote CI passes and the reviewed PR merges, deploy the exact merged commit
serially through the existing Coolify applications: synthetic development demo,
core web/API, then public demo. Verify successful deployment status, expected
container revision and health, demo `/health`, and the protected core's Access
redirect. Observe health again after the rollout. Temporarily pause automatic deployment
for these three apps before merging so their builds cannot overlap, then restore
their original enabled setting after the serial rollout. Leave per-matter drivers alone.

The agent owns rollout verification and reports evidence to the owner. Stop the
rollout on a failed build, unhealthy replacement, unexpected public 5xx response,
or lost Access boundary; use the prior successful application revision for
rollback where necessary. An intentional refusal to export unreviewed work is the
expected repair behavior, not a rollback signal. No real workflow execution is
needed to establish deployment health.

## PR review follow-up

PR #68 identified a spend-accounting gap between `SpendRecorded` and
`TurnCompleted`: without a provider-call identity, a fresh invocation with identical
usage could be mistaken for a replay. The follow-up limits deduplication on the
incomplete-turn path to explicit call identities. Legacy calls are conservatively
booked; replay of an already completed record retains its existing compatibility
behavior. Fault-injection tests cover both anonymous fresh invocations and exact
identified replays. The new legacy-call regression failed before the fix ($2 recorded instead of $4).
After the fix, 13 targeted tests passed, followed by full `make check`: 1,280
tests passed, one paid-oracle test deselected, strict mypy and Ruff passed, and
coverage remained 91%. No justified code findings remain unresolved.
