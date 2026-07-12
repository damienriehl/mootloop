"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { continueRun, pauseRun, raiseCap, resumeRun } from "@/lib/api/runs";
import { keys } from "@/lib/api/keys";
import { LockContentionError } from "@/lib/api/errors";
import type { RunStatus, RunStatusSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";

interface Props {
  matterId: string;
  runId: string;
  status: RunStatus;
}

type Action = "pause" | "resume" | "continue" | "raise-cap";

/** Run controls with OPTIMISTIC mutations + typed-409 conflict handling (FD-8/FD-9). */
export function RunControls({ matterId, runId, status }: Props) {
  const client = useQueryClient();
  const detailKey = keys.matter(matterId).run(runId).detail();
  const [error, setError] = useState<string | null>(null);
  const [capInput, setCapInput] = useState("");

  /** Optimistically patch the cached run status, snapshot for rollback. */
  async function optimisticStatus(next: RunStatus) {
    await client.cancelQueries({ queryKey: detailKey });
    const prev = client.getQueryData<RunStatusSummary>(detailKey);
    if (prev) client.setQueryData<RunStatusSummary>(detailKey, { ...prev, status: next });
    return prev;
  }

  function onError(err: unknown, prev: RunStatusSummary | undefined) {
    if (prev) client.setQueryData(detailKey, prev);
    if (err instanceof LockContentionError) {
      setError(
        err.retriable
          ? "Another change landed first — the run lock is held. Retry in a moment."
          : "This action conflicts with the current run state.",
      );
    } else {
      setError((err as Error).message);
    }
  }

  const pause = useMutation({
    mutationFn: () => pauseRun({ matterId, runId }),
    onMutate: () => {
      setError(null);
      return optimisticStatus("paused");
    },
    onError: (err, _v, prev) => onError(err, prev as RunStatusSummary | undefined),
    onSettled: () => client.invalidateQueries({ queryKey: detailKey }),
  });

  const resume = useMutation({
    mutationFn: () => resumeRun({ matterId, runId }),
    onMutate: () => {
      setError(null);
      return optimisticStatus("running");
    },
    onError: (err, _v, prev) => onError(err, prev as RunStatusSummary | undefined),
    onSettled: () => client.invalidateQueries({ queryKey: detailKey }),
  });

  const cont = useMutation({
    mutationFn: () => continueRun({ matterId, runId }),
    onMutate: () => {
      setError(null);
      return optimisticStatus("running");
    },
    onError: (err, _v, prev) => onError(err, prev as RunStatusSummary | undefined),
    onSettled: () => client.invalidateQueries({ queryKey: detailKey }),
  });

  const bumpCap = useMutation({
    mutationFn: () => {
      const to = Number.parseFloat(capInput);
      if (!Number.isFinite(to) || to <= 0) throw new Error("Enter a positive dollar cap.");
      return raiseCap({ matterId, runId }, { to_usd: to });
    },
    onMutate: () => {
      setError(null);
      return optimisticStatus("running");
    },
    onError: (err, _v, prev) => onError(err, prev as RunStatusSummary | undefined),
    onSuccess: () => setCapInput(""),
    onSettled: () => client.invalidateQueries({ queryKey: detailKey }),
  });

  const busy = pause.isPending || resume.isPending || cont.isPending || bumpCap.isPending;
  const pending = (a: Action) =>
    (a === "pause" && pause.isPending) ||
    (a === "resume" && resume.isPending) ||
    (a === "continue" && cont.isPending) ||
    (a === "raise-cap" && bumpCap.isPending);

  return (
    <div className="border border-rule bg-paper-raised p-4 shadow-ledger">
      <h2 className="mb-3 font-mono text-[0.62rem] uppercase tracking-[0.12em] text-ink-faint">
        Run controls
      </h2>
      <div className="flex flex-wrap items-center gap-2">
        {status === "paused" ? (
          <ControlButton onClick={() => resume.mutate()} disabled={busy} pending={pending("resume")}>
            Resume
          </ControlButton>
        ) : (
          <ControlButton
            onClick={() => pause.mutate()}
            disabled={busy || status !== "running"}
            pending={pending("pause")}
          >
            Pause
          </ControlButton>
        )}

        <ControlButton
          onClick={() => cont.mutate()}
          disabled={busy || status !== "checkpoint"}
          pending={pending("continue")}
        >
          Continue
        </ControlButton>

        <div className="flex items-center gap-1">
          <label htmlFor="cap" className="sr-only">
            New cap in dollars
          </label>
          <input
            id="cap"
            inputMode="decimal"
            value={capInput}
            onChange={(e) => setCapInput(e.target.value)}
            placeholder="cap $"
            className="w-20 border border-rule-strong bg-paper px-2 py-1 font-mono text-sm"
          />
          <ControlButton
            onClick={() => bumpCap.mutate()}
            disabled={busy || capInput.trim() === ""}
            pending={pending("raise-cap")}
          >
            Raise cap
          </ControlButton>
        </div>
      </div>

      {error && (
        <p role="alert" aria-live="assertive" className="mt-3 font-mono text-sm text-fail">
          {error}
        </p>
      )}
    </div>
  );
}

function ControlButton({
  children,
  onClick,
  disabled,
  pending,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
  pending?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={cn(
        "border border-rule-strong bg-paper px-3 py-1.5 font-mono text-sm text-ink transition-colors",
        "hover:border-accent hover:text-accent",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-rule-strong disabled:hover:text-ink",
      )}
    >
      {pending ? "…" : children}
    </button>
  );
}
