# Read-only State Confirmation — 2026-08-28

## Verdict

The repository and remote delivery state are healthy. The controlled Chrome profile is
now authenticated to the synthetic matter tier, which removed the former A-02 human
authentication blocker. The authenticated phone-width journey exposed one responsive
defect: the Runs list expands to 590 pixels in a 390-pixel viewport. A local-only fix
and focused regression test are ready on `fix/u17a-mobile-runs-overflow`; production
was not changed, so U-17A remains open until that fix is delivered, deployed, and
rechecked.

After the local fix, no additional work is currently admissible without a remote
mutation, fresh protected-data authorization, an operator secret action, a pending
approval, or an attorney judgment.

## Current evidence

| Surface | Read-only observation |
|---|---|
| Repository | `main` and `origin/main` are `cf987f6`; the only pre-existing dirty path is timer-owned `.claude/RESUME.md`, which this work did not touch. |
| GitHub | No open pull requests. Latest `main` CI run `32728147891` completed successfully for `cf987f6`. |
| Public production | `https://mootloop.org/` and `https://www.mootloop.org/` returned HTTP 200 with valid TLS. Both `/health` endpoints returned HTTP 200 and `{"status":"ok","version":"0.0.0"}`. |
| Protected edge | Anonymous HTTPS returned 302 through Cloudflare Access. The existing controlled Chrome session reached the exact synthetic U-17A route without another login prompt. |
| Authenticated phone QA | At 390 by 844, the run cockpit, Begin Task, Decision Inbox, and Export room had no horizontal overflow and emitted no browser warnings or errors. No button, decision, attestation, upload, export, or run action was invoked. |
| Runs-list finding | The authenticated Runs index measured 590 pixels of content width at a 390-pixel viewport. Long run metadata and its fixed-width status row caused the overflow. |
| Local repair | The list now stacks metadata below the run identity on phone widths, permits long IDs to wrap, and preserves the horizontal row at `sm` and wider. A focused responsive-layout test was added; the complete frontend gate passed with 43 tests. |
| Local browser verification | Chrome rendered the repaired Runs index against a localhost-only synthetic mock: at 390 pixels, content width was 390 with metadata stacked below the identity; at 640 pixels, content width was 640 and both blocks were center-aligned on one row. Neither width overflowed, and the console was clean. |

Only synthetic matter `2026-08-22-synthetic-u17a` was inspected. No protected matter,
secret value, credential store, browser storage, or private roster data was read. All
GitHub, production, and browser checks were read-only, and no remote system was
mutated.

## Durable queue

- Deliver, deploy, and repeat the phone-width Runs-list check after remote mutation is
  authorized; this is the remaining U-17A tail.
- U-17B remains behind fresh D-03 authorization for the specifically named protected
  read/run. U-17C remains behind the attorney's quality/confidentiality judgment.
- PR #30/#31 monitoring tails advance only during future authorized operations; do not
  manufacture operations for monitoring.
- D-09P and FD6-01P remain explicit approval gates. A-01 Coolify token repair and A-03
  Namecheap credential rotation remain operator actions.
- U-11B and U-12 through U-16 remain sequenced after the clean compounding loop under
  D-10/D-16. The D-06 Google lane remains consent-gated, and D-17 breadth remains
  deferred.

This confirmation supersedes the 2026-08-24 A-02 authentication status while
preserving every other gate in the existing completion audit and operator handoff.
