"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { attestRun } from "@/lib/api/decisions";
import { keys } from "@/lib/api/keys";
import type { Attestation } from "@/lib/api/types";

/**
 * Attestation — a DISTINCT, deliberate full-screen act that is NEVER optimistic
 * (FD-8/FD-9). The colophon (run id, master + ledger-head hashes, reviewer, timestamp)
 * is shown ONLY after the server records the attestation; nothing in the UI reflects
 * a completed attestation until `attestRun` resolves.
 */
export function AttestPanel({
  matterId,
  runId,
  blocked,
  blockReason,
}: {
  matterId: string;
  runId: string;
  blocked: boolean;
  blockReason?: string;
}) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<Attestation | null>(null);
  const [error, setError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: () => attestRun({ matterId, runId }),
    onSuccess: (attestation) => {
      // Only NOW does anything reflect the attestation — never optimistic.
      setResult(attestation);
      void client.invalidateQueries({ queryKey: keys.matter(matterId).run(runId).detail() });
    },
    onError: (err) => setError((err as Error).message),
  });

  return (
    <>
      <div className="border border-rule border-l-4 border-l-accent bg-paper-raised p-5 shadow-ledger">
        <h2 className="font-bold [font-variant:small-caps]">Certify &amp; release</h2>
        <p className="mt-1 text-sm text-ink-soft">
          Attestation binds the work product to its ledger. This is a deliberate act — it
          is recorded only when you confirm on the certification screen.
        </p>
        <button
          type="button"
          onClick={() => {
            setError(null);
            setResult(null);
            setOpen(true);
          }}
          disabled={blocked}
          className="mt-3 border border-accent bg-accent-soft px-4 py-2 font-mono text-sm font-bold text-accent hover:bg-accent hover:text-paper disabled:cursor-not-allowed disabled:opacity-40"
        >
          Attest this run
        </button>
        {blocked && (
          <p className="mt-2 font-mono text-[0.72rem] text-pending">
            {blockReason ?? "Resolve all blocking decisions and clear gates before attesting."}
          </p>
        )}
      </div>

      {open && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Certify and release"
          className="fixed inset-0 z-50 flex items-center justify-center bg-ink/60 p-4"
        >
          <div className="ink-in max-w-lg border border-rule-strong bg-paper p-6 shadow-ledger">
            {!result ? (
              <>
                <h3 className="text-lg font-bold text-accent [font-variant:small-caps]">
                  Attestation
                </h3>
                <p className="mt-3 text-sm text-ink-soft">
                  You are certifying run <span className="font-mono text-ink">{runId}</span>. The
                  attestation records the current master and ledger-head hashes against your
                  identity and is logged to the access audit. This cannot be undone from here.
                </p>
                {error && (
                  <p role="alert" aria-live="assertive" className="mt-3 font-mono text-sm text-fail">
                    {error}
                  </p>
                )}
                <div className="mt-5 flex justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    disabled={mutation.isPending}
                    className="border border-rule-strong px-4 py-2 font-mono text-sm text-ink-soft"
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => mutation.mutate()}
                    disabled={mutation.isPending}
                    className="border border-accent bg-accent px-4 py-2 font-mono text-sm font-bold text-paper disabled:opacity-60"
                  >
                    {mutation.isPending ? "Recording…" : "Certify & release"}
                  </button>
                </div>
              </>
            ) : (
              // Colophon — rendered only from the server's recorded attestation.
              <>
                <h3 className="text-lg font-bold text-pass [font-variant:small-caps]">
                  Certified
                </h3>
                <dl className="mt-4 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 font-mono text-xs">
                  <dt className="text-ink-faint">run</dt>
                  <dd className="break-all">{result.run_id}</dd>
                  <dt className="text-ink-faint">master</dt>
                  <dd className="break-all">{result.master_sha256}</dd>
                  <dt className="text-ink-faint">ledger head</dt>
                  <dd className="break-all">{result.ledger_head_sha256}</dd>
                  <dt className="text-ink-faint">reviewer</dt>
                  <dd className="break-all">{result.reviewer}</dd>
                  <dt className="text-ink-faint">attested</dt>
                  <dd className="break-all">{result.attested_at}</dd>
                </dl>
                <div className="mt-5 flex justify-end">
                  <button
                    type="button"
                    onClick={() => setOpen(false)}
                    className="border border-accent bg-accent px-4 py-2 font-mono text-sm font-bold text-paper"
                  >
                    Close
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
