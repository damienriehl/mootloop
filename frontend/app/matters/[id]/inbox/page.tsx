"use client";

import { useParams, useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getDecisions } from "@/lib/api/decisions";
import { listRuns } from "@/lib/api/runs";
import { keys } from "@/lib/api/keys";
import { DecisionCard } from "@/components/inbox/DecisionCard";
import { AttestPanel } from "@/components/inbox/AttestPanel";
import type { Decision } from "@/lib/api/types";

export default function InboxPage() {
  const { id: matterId } = useParams<{ id: string }>();
  const search = useSearchParams();
  const runParam = search.get("run");

  // The inbox is per-run; default to the most recent run when none is specified.
  const { data: runs } = useQuery({
    queryKey: keys.matter(matterId).runs(),
    queryFn: () => listRuns(matterId),
  });
  const runId = runParam ?? runs?.[runs.length - 1]?.run_id ?? null;

  const { data, isLoading, isError, error } = useQuery({
    queryKey: runId ? keys.matter(matterId).run(runId).decisions() : ["inbox", "none"],
    queryFn: () => getDecisions({ matterId, runId: runId as string }),
    enabled: runId != null,
  });

  const decisions = data?.decisions ?? [];
  // Blocking = still open (blocks attestation); Entered = already resolved (parallel).
  const blocking: Decision[] = decisions.filter((d) => d.status === "open");
  const entered: Decision[] = decisions.filter((d) => d.status !== "open");

  return (
    <section className="grid gap-6">
      <header className="border-b border-rule pb-3">
        <p className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-ink-faint">
          Decision inbox
        </p>
        <h1 className="text-lg font-bold text-accent [font-variant:small-caps]">
          {runId ? `Run ${runId}` : "No run selected"}
        </h1>
      </header>

      {isLoading && <p className="font-mono text-sm text-ink-faint">Loading decisions…</p>}
      {isError && (
        <p role="alert" className="font-mono text-sm text-fail">
          {(error as Error).message}
        </p>
      )}
      {runId == null && !isLoading && (
        <p className="font-mono text-sm text-ink-faint">This matter has no runs yet.</p>
      )}

      {runId && (
        <>
          <div>
            <h2 className="mb-2 font-mono text-[0.7rem] uppercase tracking-[0.1em] text-pending">
              Blocking — awaiting your ruling ({blocking.length})
            </h2>
            <div className="grid gap-3">
              {blocking.map((d) => (
                <DecisionCard key={d.decision_id} matterId={matterId} runId={runId} decision={d} />
              ))}
              {blocking.length === 0 && (
                <p className="font-mono text-sm text-ink-faint">No decisions await a ruling.</p>
              )}
            </div>
          </div>

          {entered.length > 0 && (
            <div>
              <h2 className="mb-2 font-mono text-[0.7rem] uppercase tracking-[0.1em] text-pass">
                Entered ({entered.length})
              </h2>
              <div className="grid gap-3">
                {entered.map((d) => (
                  <DecisionCard
                    key={d.decision_id}
                    matterId={matterId}
                    runId={runId}
                    decision={d}
                  />
                ))}
              </div>
            </div>
          )}

          <AttestPanel
            matterId={matterId}
            runId={runId}
            blocked={blocking.length > 0}
            blockReason={
              blocking.length > 0
                ? `${blocking.length} decision(s) still block attestation.`
                : undefined
            }
          />
        </>
      )}
    </section>
  );
}
