"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { listRuns } from "@/lib/api/runs";
import { keys } from "@/lib/api/keys";
import { StatusStamp } from "@/components/ui/StatusStamp";
import { usd } from "@/lib/format";

export default function RunsIndexPage() {
  const { id: matterId } = useParams<{ id: string }>();
  const { data, isLoading, isError, error } = useQuery({
    queryKey: keys.matter(matterId).runs(),
    queryFn: () => listRuns(matterId),
  });

  return (
    <section>
      <h1 className="mb-1 text-lg font-bold tracking-wide text-accent [font-variant:small-caps]">
        Runs
      </h1>
      <p className="mb-6 max-w-prose text-ink-soft">
        The matter&rsquo;s runs. Open one to watch the pipeline ink in live.
      </p>

      {isLoading && <p className="font-mono text-sm text-ink-faint">Loading runs…</p>}
      {isError && (
        <p role="alert" className="font-mono text-sm text-fail">
          {(error as Error).message}
        </p>
      )}

      <ul className="grid gap-3">
        {data?.map((run) => (
          <li key={run.run_id}>
            <Link
              href={`/matters/${matterId}/runs/${run.run_id}`}
              className="flex items-center justify-between gap-4 border border-rule border-l-4 border-l-accent bg-paper-raised px-5 py-4 no-underline shadow-ledger transition-colors hover:bg-accent-soft"
            >
              <div className="min-w-0">
                <span className="font-mono font-bold text-ink">{run.run_id}</span>
                <span className="mt-1 block truncate font-mono text-[0.7rem] uppercase tracking-[0.08em] text-ink-faint">
                  {run.task ?? "—"} · {run.mode} · {run.current_stage ?? "—"}
                </span>
              </div>
              <div className="flex shrink-0 items-center gap-4">
                <span className="font-mono text-sm text-ink-soft">{usd(run.total_spend_usd)}</span>
                <StatusStamp status={run.status} />
              </div>
            </Link>
          </li>
        ))}
        {data && data.length === 0 && (
          <li className="font-mono text-sm text-ink-faint">No runs yet.</li>
        )}
      </ul>
    </section>
  );
}
