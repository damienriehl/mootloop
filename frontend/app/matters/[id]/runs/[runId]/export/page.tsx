"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { getDeliverables } from "@/lib/api/export";
import { getRun } from "@/lib/api/runs";
import { keys } from "@/lib/api/keys";
import { DeliverableRow } from "@/components/export/DeliverableRow";
import type { ColophonFields } from "@/components/export/ColophonCard";

/**
 * Export room v1 (FE-2.5) — the certify-and-release surface (FD-9 / P-37). Deliverables
 * list with DRAFT/clean state from the server; each releasable file opens a colophon and
 * mints an audit-logged signed link. Query cache is the single source of truth.
 *
 * Colophon fields the v1 read endpoints expose are shown live; those they do NOT
 * (rubric version, citator disclosure) are shown honestly as pending rather than
 * fabricated. Attestation state is derived from the export-ready gate (the run-detail
 * read carries no attestation record yet).
 */
export default function ExportRoomPage() {
  const { id: matterId, runId } = useParams<{ id: string; runId: string }>();
  const runKeys = keys.matter(matterId).run(runId);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: runKeys.deliverables(),
    queryFn: () => getDeliverables({ matterId, runId }),
  });

  const { data: run } = useQuery({
    queryKey: runKeys.detail(),
    queryFn: () => getRun({ matterId, runId }),
  });

  const exportReady = data?.export_ready ?? false;
  const deliverables = data?.deliverables ?? [];

  const colophon: ColophonFields = {
    runId,
    rubricVersion: null, // Not exposed by the v1 run-detail read (honest pending).
    attestationState: exportReady ? "export-ready — release permitted" : "pending — gates open",
    attestationTone: exportReady ? "pass" : "pending",
    citatorDisclosure: null, // Not exposed by the v1 run-detail read (honest pending).
  };

  return (
    <section className="grid gap-6">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-rule pb-3">
        <div>
          <p className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-ink-faint">
            Export &amp; release
          </p>
          <h1 className="font-mono text-lg font-bold text-accent">{runId}</h1>
        </div>
        <Link
          href={`/matters/${matterId}/runs/${runId}`}
          className="font-mono text-sm text-ink-soft hover:text-accent"
        >
          ← Back to cockpit
        </Link>
      </header>

      <div
        data-testid="export-gate"
        className={`border px-4 py-2 font-mono text-[0.72rem] uppercase tracking-[0.08em] ${
          exportReady ? "border-pass text-pass" : "border-pending text-pending"
        }`}
      >
        {exportReady
          ? "Run is export-ready — clean deliverables may be released."
          : "Run is not export-ready — only DRAFT deliverables may be released."}
      </div>

      {isLoading && <p className="font-mono text-sm text-ink-faint">Loading deliverables…</p>}
      {isError && (
        <p role="alert" className="font-mono text-sm text-fail">
          {(error as Error).message}
        </p>
      )}

      <ul className="grid gap-3">
        {deliverables.map((file) => (
          <DeliverableRow
            key={file.name}
            matterId={matterId}
            runId={runId}
            file={file}
            exportReady={exportReady}
            colophon={colophon}
          />
        ))}
        {data && deliverables.length === 0 && (
          <li className="font-mono text-sm text-ink-faint">
            No deliverables yet — they appear as the run produces work product.
          </li>
        )}
      </ul>

      {run == null && !isLoading && (
        <p className="font-mono text-[0.68rem] text-ink-faint">Run detail unavailable.</p>
      )}
    </section>
  );
}
