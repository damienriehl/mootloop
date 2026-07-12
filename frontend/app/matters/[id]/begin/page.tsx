"use client";

import { useRouter, useParams } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { createFreeformTask } from "@/lib/api/tasks";
import { startRun } from "@/lib/api/runs";
import { keys } from "@/lib/api/keys";
import { useBeginDraftStore } from "@/lib/stores/beginDraft";
import { TaskSlipCard } from "@/components/onramp/TaskSlipCard";
import type { TaskSpecResponse } from "@/lib/api/types";

/**
 * Begin-task OMNIBOX (FD-9) — the FREEFORM lane only (FE-2.5). A single serif "sentence"
 * input in the argument voice; on submit the intent resolves to a TaskSpec slip preview.
 * The wizard/suggestion input-shape branches (empty=suggestions, keywords=catalog) land
 * in FE-3; this page is deliberately the sentence→freeform lane alone.
 *
 * Query cache stays the single source of truth (no useState mirror of server data): the
 * resolved slip lives in the freeform mutation's `data`; in-progress input persists to a
 * Zustand draft store (localStorage) per FD-8.
 */
export default function BeginTaskPage() {
  const { id: matterId } = useParams<{ id: string }>();
  const router = useRouter();
  const client = useQueryClient();

  const intent = useBeginDraftStore((s) => s.intents[matterId] ?? "");
  const setIntent = useBeginDraftStore((s) => s.setIntent);
  const clearIntent = useBeginDraftStore((s) => s.clearIntent);

  const [startError, setStartError] = useState<string | null>(null);

  // Resolve the free-text intent into a TaskSpec slip. `data` is the slip (cache truth).
  const resolve = useMutation({
    mutationFn: (text: string) => createFreeformTask(matterId, text),
    onSuccess: () => {
      // The append-only TaskSpec list gained an entry — invalidate any consumer.
      void client.invalidateQueries({ queryKey: keys.matter(matterId).tasks() });
    },
  });

  // Confirm on a RUNNABLE slip → start the run with its task_spec_id → route to cockpit.
  const start = useMutation({
    mutationFn: (res: TaskSpecResponse) =>
      // Confirm is only offered for RUNNABLE slips, so `task` is a resolved adapter key
      // here; the fallback satisfies the required field and never fires in practice.
      startRun(matterId, {
        task: res.task_spec.task ?? "discovery-responses",
        task_spec_id: res.task_spec.task_spec_id,
      }),
    onError: (err) => setStartError((err as Error).message),
    onSuccess: (run) => {
      clearIntent(matterId);
      void client.invalidateQueries({ queryKey: keys.matter(matterId).runs() });
      router.push(`/matters/${matterId}/runs/${run.run_id}`);
    },
  });

  const slip = resolve.data;
  const trimmed = intent.trim();

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (trimmed === "" || resolve.isPending) return;
    setStartError(null);
    resolve.mutate(trimmed);
  }

  function revise() {
    resolve.reset();
    setStartError(null);
  }

  return (
    <section className="grid gap-6">
      <header className="border-b border-rule pb-3">
        <p className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-ink-faint">
          Begin a task
        </p>
        <h1 className="text-lg font-bold text-accent [font-variant:small-caps]">
          What do you need drafted?
        </h1>
      </header>

      {/* The omnibox — a serif "sentence" in the argument voice (FD-9). */}
      <form onSubmit={onSubmit} className="grid gap-3">
        <label htmlFor="intent" className="sr-only">
          Describe the task in a sentence
        </label>
        <textarea
          id="intent"
          data-testid="omnibox-input"
          value={intent}
          onChange={(e) => setIntent(matterId, e.target.value)}
          rows={3}
          placeholder="Draft responses and objections to the plaintiff's first set of requests for production…"
          className="w-full resize-none border border-rule-strong bg-paper px-4 py-3 font-serif text-lg leading-snug shadow-ledger placeholder:text-ink-faint"
          disabled={resolve.isPending || slip != null}
        />
        <div className="flex flex-wrap items-center gap-3">
          <button
            type="submit"
            data-testid="omnibox-submit"
            disabled={trimmed === "" || resolve.isPending || slip != null}
            className="border border-accent bg-accent-soft px-4 py-2 font-mono text-sm font-bold text-accent transition-colors hover:bg-accent hover:text-paper disabled:cursor-not-allowed disabled:opacity-40"
          >
            {resolve.isPending ? "Resolving…" : "Resolve to a task slip"}
          </button>
          <p className="font-mono text-[0.68rem] text-ink-faint">
            Freeform lane · the catalog &amp; suggestion lanes arrive in FE-3.
          </p>
        </div>
      </form>

      {resolve.isError && (
        <p role="alert" aria-live="assertive" className="font-mono text-sm text-fail">
          {(resolve.error as Error).message}
        </p>
      )}

      {slip && (
        <TaskSlipCard
          spec={slip.task_spec}
          runnable={slip.runnable}
          pending={start.isPending}
          error={startError}
          onConfirm={() => {
            setStartError(null);
            start.mutate(slip);
          }}
          onDiscard={revise}
        />
      )}
    </section>
  );
}
