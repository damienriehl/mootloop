# Off-box backup retention hold — 2026-09-30

Disposition: **NEEDS-DAMIEN**. The September 30 answer explicitly defers FD6-01P A
versus B until the governing records-retention schedule is confirmed. Real-matter
use remains **NOT YET**. The approved D-09 AWS integrity ledger covers content-free
heads and receipts only; it does not authorize encrypted matter-backup retention.

The bounded next decision is already prepared in
`docs/decisions/2026-08-24-fd6-01-off-box-backup.md`, especially its Approval requested
section. Once the firm confirms the schedule, Damien supplies that schedule's
reference and chooses A only if its immutable retention terms are compatible,
including retention after a matter's destruction date; otherwise choose B with the
packet's exact alternative parameters. Preserve its RPO/RTO, escrow, restore-drill,
client-encryption and independent-verifier conditions. Do not infer that seven-year
D-09 approval answers this separate question.

No bucket creation, upload, credential action, backup purge, retention change, or
restore was performed. No fresh price or remote availability is asserted. After
approval, the agent can prepare/review the selected implementation and current cost
estimate before any separately authorized provisioning or spend. The existing
`src/mootloop/engine/backup.py` and `tests/unit/test_engine_backup.py` cover local
backup behavior; passing them would not prove an off-box destination or a restore
from that destination.

Keep the original Waiting card open. No operation should be manufactured to close it.
Retained immutable objects could not be deleted as rollback; a future rollout must
stop new uploads and preserve already retained versions under the approved schedule.
This documentation is reversible and belongs on default `main` independently of
homepage work.
