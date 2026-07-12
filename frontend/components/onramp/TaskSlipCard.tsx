"use client";

import { cn } from "@/lib/utils/cn";
import type { TaskSpec } from "@/lib/api/types";

/**
 * The TaskSpec SLIP as a pleading-caption card (FD-9). Record fields (task key, lane,
 * spec id, created_at, FOLIO ref) are set in the MONO record voice; the attorney's
 * intent reads in the SERIF argument voice. When the intent did not resolve to a
 * runnable task (`task === null`), the slip states an HONEST unmapped posture — no fake
 * start button — pointing at FE-3 synthesis; a runnable slip offers "Start run".
 */
export function TaskSlipCard({
  spec,
  runnable,
  onConfirm,
  onDiscard,
  pending,
  error,
}: {
  spec: TaskSpec;
  runnable: boolean;
  onConfirm: () => void;
  onDiscard: () => void;
  pending: boolean;
  error?: string | null;
}) {
  return (
    <article
      data-testid="task-slip"
      className="ink-in border border-rule border-l-4 border-l-accent bg-paper-raised shadow-ledger"
    >
      {/* Caption head — the pleading-caption motif */}
      <header className="border-b border-double border-rule-strong px-5 py-4">
        <div className="flex items-baseline justify-between gap-3">
          <p className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-ink-faint">
            Task slip · freeform lane
          </p>
          <span
            data-testid="slip-lane-state"
            className={cn(
              "rounded-[3px] border px-1.5 py-0.5 font-mono text-[0.6rem] uppercase tracking-[0.08em]",
              runnable ? "border-pass text-pass" : "border-pending text-pending",
            )}
          >
            {runnable ? "runnable" : "unmapped"}
          </span>
        </div>
        {/* Intent — argument voice (serif). */}
        <p data-testid="slip-intent" className="mt-2 text-lg leading-snug">
          {spec.intent_text}
        </p>
      </header>

      {/* Record fields — mono record voice. */}
      <dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1.5 px-5 py-4 font-mono text-xs">
        <dt className="text-ink-faint">task</dt>
        <dd data-testid="slip-task" className="break-all text-ink">
          {spec.task ?? "— (unmapped)"}
        </dd>
        <dt className="text-ink-faint">lane</dt>
        <dd className="text-ink-soft">{spec.source_lane}</dd>
        <dt className="text-ink-faint">spec id</dt>
        <dd className="break-all text-ink-soft">{spec.task_spec_id}</dd>
        <dt className="text-ink-faint">folio</dt>
        <dd className="break-all text-ink-soft">{spec.folio_label ?? spec.folio_iri ?? "—"}</dd>
        <dt className="text-ink-faint">utbms</dt>
        <dd className="text-ink-soft">{spec.utbms ?? "—"}</dd>
        <dt className="text-ink-faint">created</dt>
        <dd className="break-all text-ink-soft">{spec.created_at}</dd>
      </dl>

      <footer className="border-t border-rule px-5 py-4">
        {runnable ? (
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              data-testid="slip-start"
              onClick={onConfirm}
              disabled={pending}
              className="border border-accent bg-accent px-4 py-2 font-mono text-sm font-bold text-paper transition-colors hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {pending ? "Starting…" : "Start run"}
            </button>
            <button
              type="button"
              onClick={onDiscard}
              disabled={pending}
              className="border border-rule-strong px-4 py-2 font-mono text-sm text-ink-soft hover:border-accent hover:text-accent disabled:opacity-40"
            >
              Revise intent
            </button>
          </div>
        ) : (
          <div data-testid="slip-unmapped" className="grid gap-3">
            <p className="text-sm text-ink-soft">
              This intent is recorded, but it did not resolve to a runnable task. It will be
              startable when concept synthesis lands (FE-3) — no run can start from an unmapped
              slip today.
            </p>
            <div>
              <button
                type="button"
                onClick={onDiscard}
                className="border border-rule-strong px-4 py-2 font-mono text-sm text-ink-soft hover:border-accent hover:text-accent"
              >
                Revise intent
              </button>
            </div>
          </div>
        )}
        {error && (
          <p role="alert" aria-live="assertive" className="mt-3 font-mono text-sm text-fail">
            {error}
          </p>
        )}
      </footer>
    </article>
  );
}
