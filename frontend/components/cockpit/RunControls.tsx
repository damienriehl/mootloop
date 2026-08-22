"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  continueRun,
  pauseRun,
  queueCitationChecks,
  queueJudgeProfile,
  raiseCap,
  reopenRun,
  resumeRun,
} from "@/lib/api/runs";
import { keys } from "@/lib/api/keys";
import { LockContentionError } from "@/lib/api/errors";
import type { AttentionBlocker, RunStatus, RunStatusSummary } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";

interface Props {
  matterId: string;
  runId: string;
  status: RunStatus;
  attentionBlockers?: AttentionBlocker[];
}

type Action =
  | "pause"
  | "resume"
  | "continue"
  | "reopen"
  | "raise-cap"
  | "check-citations"
  | "judge-profile";

/** Run controls with OPTIMISTIC mutations + typed-409 conflict handling (FD-8/FD-9). */
export function RunControls({ matterId, runId, status, attentionBlockers = [] }: Props) {
  const client = useQueryClient();
  const detailKey = keys.matter(matterId).run(runId).detail();
  const [error, setError] = useState<string | null>(null);
  const [capInput, setCapInput] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [reopenReason, setReopenReason] = useState("");
  const [grantAttempts, setGrantAttempts] = useState("0");

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

  const reopen = useMutation({
    mutationFn: () => {
      const grant = Number(grantAttempts);
      if (!Number.isInteger(grant) || grant < 0) {
        throw new Error("Retry grant must be a whole number of zero or more.");
      }
      return reopenRun(
        { matterId, runId },
        { reason: reopenReason.trim(), grant_attempts: grant },
      );
    },
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onError: (err) => onError(err, undefined),
    onSuccess: () => {
      setReopenReason("");
      setGrantAttempts("0");
      setNotice("Run reopened and its canonical queue work item is ready.");
    },
    onSettled: () => client.invalidateQueries({ queryKey: detailKey }),
  });

  const checkCitations = useMutation({
    mutationFn: () => queueCitationChecks({ matterId, runId }),
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onError: (err) => onError(err, undefined),
    onSuccess: () => setNotice("Citation-support checks queued."),
    onSettled: () => client.invalidateQueries({ queryKey: keys.matter(matterId).run(runId).gates() }),
  });

  const judgeProfile = useMutation({
    mutationFn: () => queueJudgeProfile(matterId),
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onError: (err) => onError(err, undefined),
    onSuccess: () => setNotice("Assigned-judge public-opinion profile queued."),
  });

  const busy =
    pause.isPending ||
    resume.isPending ||
    cont.isPending ||
    reopen.isPending ||
    bumpCap.isPending ||
    checkCitations.isPending ||
    judgeProfile.isPending;
  const pending = (a: Action) =>
    (a === "pause" && pause.isPending) ||
    (a === "resume" && resume.isPending) ||
    (a === "continue" && cont.isPending) ||
    (a === "reopen" && reopen.isPending) ||
    (a === "raise-cap" && bumpCap.isPending) ||
    (a === "check-citations" && checkCitations.isPending) ||
    (a === "judge-profile" && judgeProfile.isPending);

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

        <ControlButton
          onClick={() => checkCitations.mutate()}
          disabled={busy || !["finished", "needs_decisions"].includes(status)}
          pending={pending("check-citations")}
        >
          Check citation support
        </ControlButton>

        <ControlButton
          onClick={() => judgeProfile.mutate()}
          disabled={busy}
          pending={pending("judge-profile")}
        >
          Build judge profile
        </ControlButton>
      </div>

      {status === "needs_attention" && (
        <section className="mt-4 border-t border-rule pt-4" aria-labelledby="reopen-heading">
          <h3 id="reopen-heading" className="font-mono text-sm font-bold text-fail">
            Operator repair required
          </h3>
          {attentionBlockers.length > 0 ? (
            <ul className="mt-2 list-disc space-y-1 pl-5 font-mono text-sm text-ink-soft">
              {attentionBlockers.map((blocker) => (
                <li key={`${blocker.kind}:${blocker.ref}`}>{blocker.detail}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-2 text-sm text-ink-soft">
              Fix the provider, credential, or runtime problem that stopped this run before
              reopening it.
            </p>
          )}
          <div className="mt-3 grid max-w-xl gap-3 sm:grid-cols-[1fr_9rem]">
            <label className="grid gap-1 font-mono text-xs text-ink-soft">
              Repair performed
              <input
                value={reopenReason}
                onChange={(event) => setReopenReason(event.target.value)}
                placeholder="Rotated provider credential"
                className="min-h-11 border border-rule-strong bg-paper px-3 text-sm text-ink"
              />
            </label>
            <label className="grid gap-1 font-mono text-xs text-ink-soft">
              Extra attempts
              <input
                type="number"
                min="0"
                step="1"
                value={grantAttempts}
                onChange={(event) => setGrantAttempts(event.target.value)}
                className="min-h-11 border border-rule-strong bg-paper px-3 text-sm text-ink"
              />
            </label>
          </div>
          <p className="mt-2 text-xs text-ink-faint">
            Reopen records your reason and attempt grant, then repairs the canonical queue item.
            A retried request repairs queue delivery without recording a second reopen.
          </p>
          <div className="mt-3">
            <ControlButton
              onClick={() => reopen.mutate()}
              disabled={busy || reopenReason.trim() === ""}
              pending={pending("reopen")}
            >
              Reopen and queue
            </ControlButton>
          </div>
        </section>
      )}

      {error && (
        <p role="alert" aria-live="assertive" className="mt-3 font-mono text-sm text-fail">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" aria-live="polite" className="mt-3 font-mono text-sm text-pass">
          {notice}
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
        "min-h-11 border border-rule-strong bg-paper px-3 py-1.5 font-mono text-sm text-ink transition-colors",
        "hover:border-accent hover:text-accent",
        "disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:border-rule-strong disabled:hover:text-ink",
      )}
    >
      {pending ? "…" : children}
    </button>
  );
}
