"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getRun, getRunGates } from "@/lib/api/runs";
import { keys } from "@/lib/api/keys";
import { useRunStream } from "@/lib/api/useRunStream";
import { pendingIntentTotal } from "@/lib/api/runStream";
import { InstrumentBand } from "@/components/cockpit/InstrumentBand";
import { PersonaStrip } from "@/components/cockpit/PersonaStrip";
import { Timeline } from "@/components/cockpit/Timeline";
import { RunControls } from "@/components/cockpit/RunControls";
import { GateChip } from "@/components/ui/GateChip";

export default function CockpitPage() {
  const { id: matterId, runId } = useParams<{ id: string; runId: string }>();
  const runKeys = keys.matter(matterId).run(runId);

  const { data: run } = useQuery({
    queryKey: runKeys.detail(),
    queryFn: () => getRun({ matterId, runId }),
  });

  const { data: gates } = useQuery({
    queryKey: runKeys.gates(),
    queryFn: () => getRunGates({ matterId, runId }),
  });

  const stream = useRunStream({ matterId, runId });
  const live = stream.seq > 0;

  const status = live ? stream.status : (run?.status ?? "running");
  const spendUsd = live ? stream.spendUsd : (run?.total_spend_usd ?? 0);
  const capUsd = stream.capUsd ?? run?.hard_cap_usd ?? null;
  const completedTurns = live ? stream.completedTurns : (run?.completed_turns ?? 0);
  const discardedTurns = live ? stream.discardedTurns : (run?.discarded_turns ?? 0);
  const stage = stream.currentStage ?? run?.current_stage ?? null;

  const turnsByPersona: Record<string, number> = {};
  for (const line of stream.lines) {
    if (line.kind === "turn_completed" && line.persona) {
      turnsByPersona[line.persona] = (turnsByPersona[line.persona] ?? 0) + 1;
    }
  }

  return (
    <section className="grid gap-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule pb-3">
        <div>
          <p className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-ink-faint">
            Run cockpit
          </p>
          <h1 className="font-mono text-lg font-bold text-accent">{runId}</h1>
        </div>
        <div className="flex items-center gap-4">
          <Link
            href={`/matters/${matterId}/inbox`}
            className="font-mono text-sm text-ink-soft hover:text-accent"
          >
            Decision inbox
            {run && (run.open_decisions?.length ?? 0) > 0 ? ` (${run.open_decisions?.length})` : ""} →
          </Link>
          <Link
            href={`/matters/${matterId}/runs/${runId}/export`}
            className="font-mono text-sm text-ink-soft hover:text-accent"
          >
            Export &amp; release →
          </Link>
        </div>
      </header>

      <InstrumentBand
        status={status}
        spendUsd={spendUsd}
        pendingUsd={pendingIntentTotal(stream)}
        capUsd={capUsd}
        completedTurns={completedTurns}
        discardedTurns={discardedTurns}
        pauseReason={stream.pauseReason}
        stage={stage}
      />

      <RunControls matterId={matterId} runId={runId} status={status} />

      <div>
        <h2 className="mb-2 font-mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-faint">
          Pipeline
        </h2>
        <PersonaStrip activePersona={stream.activePersona} turnsByPersona={turnsByPersona} />
      </div>

      {gates && (
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="font-mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-faint">
              Gates
            </h2>
            <span
              className={`font-mono text-[0.7rem] uppercase tracking-[0.08em] ${
                gates.export_ready ? "text-pass" : "text-pending"
              }`}
            >
              {gates.export_ready
                ? "export ready"
                : `${gates.blockers?.length ?? 0} blocker(s)`}
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {(gates.turn_gates ?? []).map((g, i) => (
              <GateChip key={`${g.gate}-${i}`} gate={g.gate} status={g.status} />
            ))}
            {(gates.turn_gates ?? []).length === 0 && (
              <span className="font-mono text-sm text-ink-faint">No gates recorded yet.</span>
            )}
          </div>
        </div>
      )}

      <div>
        <h2 className="mb-2 font-mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-faint">
          Iteration timeline
        </h2>
        <Timeline lines={stream.lines} />
      </div>
    </section>
  );
}
