"use client";

import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { mintDownloadLink } from "@/lib/api/export";
import { ExportNotReadyError } from "@/lib/api/errors";
import { ColophonCard, type ColophonFields } from "./ColophonCard";
import { cn } from "@/lib/utils/cn";
import type { DeliverableInfo, SignedLinkResponse } from "@/lib/api/types";

/**
 * One deliverable in the export room. DRAFT files carry a DRAFT chip and are always
 * releasable; clean files are gated on the run's export-ready predicate (disabled with
 * a reason until ready). Releasing opens the FD-9 colophon, then mints a signed link —
 * NEVER optimistically: the link + the "logged to access audit" confirmation appear only
 * after the server responds. A clean-but-not-ready mint returns the typed 403
 * (`ExportNotReadyError`) whose blockers are surfaced verbatim.
 */
export function DeliverableRow({
  matterId,
  runId,
  file,
  exportReady,
  colophon,
}: {
  matterId: string;
  runId: string;
  file: DeliverableInfo;
  exportReady: boolean;
  colophon: ColophonFields;
}) {
  const [open, setOpen] = useState(false);
  const [link, setLink] = useState<SignedLinkResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [blockers, setBlockers] = useState<string[]>([]);

  const mint = useMutation({
    mutationFn: () => mintDownloadLink({ matterId, runId, name: file.name }),
    onMutate: () => {
      // Clear prior outcome; NOTHING reflects a release until the server responds.
      setError(null);
      setBlockers([]);
      setLink(null);
    },
    onSuccess: (signed) => setLink(signed),
    onError: (err) => {
      if (err instanceof ExportNotReadyError) {
        setError(err.message);
        setBlockers(err.blockers);
      } else {
        setError((err as Error).message);
      }
    },
  });

  // DRAFT files are always releasable; clean files require the run to be export-ready.
  const gated = !file.is_draft && !exportReady;

  return (
    <li
      data-testid="deliverable-row"
      className="border border-rule border-l-4 border-l-accent bg-paper-raised shadow-ledger"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 py-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-mono font-bold text-ink">{file.name}</span>
            {file.is_draft && (
              <span
                data-testid="draft-chip"
                className="rounded-[3px] border border-pending px-1.5 py-0.5 font-mono text-[0.58rem] uppercase tracking-[0.1em] text-pending"
              >
                Draft
              </span>
            )}
          </div>
          <span className="mt-1 block font-mono text-[0.68rem] uppercase tracking-[0.08em] text-ink-faint">
            {(file.size_bytes / 1024).toFixed(1)} KB
            {!file.is_draft && (gated ? " · clean · gated" : " · clean · ready")}
          </span>
        </div>

        <button
          type="button"
          data-testid="release-toggle"
          onClick={() => setOpen((v) => !v)}
          disabled={gated}
          className={cn(
            "border px-3 py-1.5 font-mono text-sm transition-colors",
            gated
              ? "cursor-not-allowed border-rule-strong text-ink-faint opacity-50"
              : "border-accent text-accent hover:bg-accent hover:text-paper",
          )}
        >
          {open ? "Close" : "Certify & release"}
        </button>
      </div>

      {gated && (
        <p
          data-testid="gated-reason"
          className="border-t border-rule px-5 py-2 font-mono text-[0.72rem] text-pending"
        >
          A clean deliverable can be released only once the run is export-ready. Clear the
          remaining gates in the cockpit, or release the DRAFT above.
        </p>
      )}

      {open && !gated && (
        <div className="ink-in grid gap-4 border-t border-rule px-5 py-4">
          <ColophonCard fields={colophon} />

          {!link ? (
            <div className="grid gap-3">
              <button
                type="button"
                data-testid="mint-link"
                onClick={() => mint.mutate()}
                disabled={mint.isPending}
                className="justify-self-start border border-accent bg-accent px-4 py-2 font-mono text-sm font-bold text-paper disabled:opacity-60"
              >
                {mint.isPending ? "Minting…" : "Mint signed link"}
              </button>
              {error && (
                <div role="alert" aria-live="assertive" className="grid gap-1">
                  <p className="font-mono text-sm text-fail">{error}</p>
                  {blockers.length > 0 && (
                    <ul data-testid="mint-blockers" className="ml-4 list-disc font-mono text-xs text-fail">
                      {blockers.map((b) => (
                        <li key={b}>{b}</li>
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </div>
          ) : (
            // Rendered only from the server-signed link — never optimistic.
            <div data-testid="mint-result" className="ink-in grid gap-2 border border-pass bg-pass-bg p-4">
              <p className="font-mono text-[0.62rem] uppercase tracking-[0.12em] text-pass">
                Link minted · logged to access audit
              </p>
              <a
                href={link.url}
                data-testid="signed-url"
                download={link.doc}
                className="break-all font-mono text-sm text-accent underline"
              >
                {link.doc} → download
              </a>
              <p className="font-mono text-[0.68rem] text-ink-soft">
                {link.is_draft ? "DRAFT watermark applied · " : ""}expires {link.expires_at}
              </p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}
