import { StatusStamp } from "@/components/ui/StatusStamp";
import { usd } from "@/lib/format";
import type { RunStatus } from "@/lib/api/types";

interface Props {
  status: RunStatus;
  spendUsd: number;
  pendingUsd: number;
  capUsd: number | null;
  completedTurns: number;
  discardedTurns: number;
  pauseReason: string | null;
  stage: string | null;
}

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="bg-paper-raised px-4 py-3">
      <span className="block font-mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-faint">
        {label}
      </span>
      <span className="mt-1 block text-base font-bold">{children}</span>
    </div>
  );
}

/** The cockpit instrument band: status stamp, inking spend gauge, turn counter, and
 *  a seat-state slate that appears only when the run is paused (FD-9). */
export function InstrumentBand({
  status,
  spendUsd,
  pendingUsd,
  capUsd,
  completedTurns,
  discardedTurns,
  pauseReason,
  stage,
}: Props) {
  const projected = spendUsd + pendingUsd;
  const pct = capUsd && capUsd > 0 ? Math.min(100, (projected / capUsd) * 100) : null;

  return (
    <div className="border border-rule shadow-ledger">
      <div className="grid grid-cols-2 gap-px bg-rule sm:grid-cols-4">
        <div className="flex items-center bg-paper-raised px-4 py-3">
          <StatusStamp status={status} />
        </div>
        <Cell label="Stage">
          <span className="font-mono text-sm">{stage ?? "—"}</span>
        </Cell>
        <Cell label="Turns">
          <span className="font-mono">
            {completedTurns}
            {discardedTurns > 0 && (
              <span className="ml-1 text-xs font-normal text-ink-faint">
                (+{discardedTurns} discarded)
              </span>
            )}
          </span>
        </Cell>
        <Cell label="Spend">
          <span className="font-mono">{usd(spendUsd)}</span>
          {pendingUsd > 0 && (
            <span className="ml-1 font-mono text-xs font-normal text-pending">
              +{usd(pendingUsd)} reserved
            </span>
          )}
        </Cell>
      </div>

      {/* Inking spend gauge — fills like ink toward the cap (FD-9 signature motion). */}
      <div className="border-t border-rule bg-paper-raised px-4 py-3">
        <div className="mb-1 flex items-baseline justify-between font-mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-faint">
          <span>Budget</span>
          <span>
            {usd(projected)} {capUsd != null ? `/ ${usd(capUsd)} cap` : "· no cap"}
          </span>
        </div>
        <div
          className="h-3 overflow-hidden rounded-sm border border-rule bg-fail-bg"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={capUsd ?? undefined}
          aria-valuenow={projected}
          aria-label="Run spend against cap"
        >
          {pct != null && (
            <div
              className="bar-fill h-full bg-accent"
              style={{ width: `${pct}%` }}
            />
          )}
        </div>
      </div>

      {/* Seat-state slate — only when paused. */}
      {status === "paused" && (
        <div className="ink-in border-t border-pending/50 bg-pending-bg px-4 py-3">
          <p className="font-mono text-[0.7rem] uppercase tracking-[0.1em] text-pending">
            Seat paused{pauseReason ? ` — ${pauseReason}` : ""}
          </p>
          <p className="mt-1 text-sm text-ink-soft">
            The engine has backed off. Resume when capacity returns, or raise the cap.
          </p>
        </div>
      )}
    </div>
  );
}
