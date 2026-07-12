"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { resolveDecision } from "@/lib/api/decisions";
import { keys } from "@/lib/api/keys";
import { LockContentionError } from "@/lib/api/errors";
import { useNotesStore } from "@/lib/stores/notes";
import { cn } from "@/lib/utils/cn";
import type { Decision, DecisionsResponse, ResolutionAction } from "@/lib/api/types";

interface Props {
  matterId: string;
  runId: string;
  decision: Decision;
}

const STATUS_FROM_ACTION: Record<ResolutionAction, Decision["status"]> = {
  approve: "approved",
  modify: "modified",
  deny: "denied",
};

export function DecisionCard({ matterId, runId, decision }: Props) {
  const client = useQueryClient();
  const runKeys = keys.matter(matterId).run(runId);
  const decisionsKey = runKeys.decisions();
  const detailKey = runKeys.detail();

  const draft = useNotesStore((s) => s.drafts[decision.decision_id] ?? "");
  const setDraft = useNotesStore((s) => s.setDraft);
  const clearDraft = useNotesStore((s) => s.clearDraft);

  const [chosenKey, setChosenKey] = useState<string>(decision.proposal.recommended);
  const [error, setError] = useState<string | null>(null);
  // Two-step "so-ordered" ceremony for binding RFA admissions (FD-9).
  const [soOrderStage, setSoOrderStage] = useState<"idle" | "confirm">("idle");

  const isRfa = decision.kind === "rfa_disposition";
  const resolved = decision.status !== "open";

  const mutation = useMutation({
    mutationFn: (action: ResolutionAction) =>
      resolveDecision({
        matterId,
        runId,
        decisionId: decision.decision_id,
        action,
        chosenKey,
        note: draft,
      }),
    onMutate: async (action) => {
      setError(null);
      await client.cancelQueries({ queryKey: decisionsKey });
      const prev = client.getQueryData<DecisionsResponse>(decisionsKey);
      // Optimistic decide: patch this decision's status in the cached list.
      if (prev) {
        client.setQueryData<DecisionsResponse>(decisionsKey, {
          ...prev,
          decisions: (prev.decisions ?? []).map((d) =>
            d.decision_id === decision.decision_id
              ? { ...d, status: STATUS_FROM_ACTION[action] }
              : d,
          ),
        });
      }
      return prev;
    },
    onError: (err, _action, prev) => {
      if (prev) client.setQueryData(decisionsKey, prev);
      setSoOrderStage("idle");
      if (err instanceof LockContentionError) {
        setError("Another change landed first — reload and re-enter your ruling.");
      } else {
        setError((err as Error).message);
      }
    },
    onSuccess: () => {
      clearDraft(decision.decision_id);
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: decisionsKey });
      void client.invalidateQueries({ queryKey: detailKey });
    },
  });

  const options = decision.proposal.options ?? [];
  const chosenOption = options.find((o) => o.key === chosenKey);

  return (
    <article
      className={cn(
        "border border-rule border-l-4 bg-paper-raised p-5 shadow-ledger",
        resolved ? "border-l-pass" : "border-l-pending",
      )}
    >
      <header className="flex flex-wrap items-baseline gap-2">
        <span className="rounded-[3px] border border-accent px-1.5 py-0.5 font-mono text-[0.62rem] uppercase tracking-[0.08em] text-accent">
          {decision.kind.replace(/_/g, " ")}
        </span>
        <span className="font-mono text-[0.7rem] text-ink-faint">{decision.decision_id}</span>
        <span
          className={cn(
            "ml-auto font-mono text-[0.7rem] uppercase tracking-[0.08em]",
            resolved ? "text-pass" : "text-pending",
          )}
        >
          {decision.status}
        </span>
      </header>

      <h3 className="mt-3 font-bold">{decision.proposal.summary}</h3>
      <p className="mt-1 text-sm text-ink-soft">{decision.proposal.reasoning}</p>

      {/* Options — the recommended one is flagged IN TEXT, not by color alone (FD-9/a11y). */}
      <fieldset className="mt-4" disabled={resolved || mutation.isPending}>
        <legend className="sr-only">Choose a disposition</legend>
        <ul className="grid gap-2">
          {options.map((opt) => {
            const recommended = opt.key === decision.proposal.recommended;
            return (
              <li key={opt.key}>
                <label className="flex cursor-pointer gap-2">
                  <input
                    type="radio"
                    name={`opt-${decision.decision_id}`}
                    value={opt.key}
                    checked={chosenKey === opt.key}
                    onChange={() => {
                      setChosenKey(opt.key);
                      setSoOrderStage("idle");
                    }}
                    className="mt-1"
                  />
                  <span className="text-sm">
                    <span className="font-bold">
                      {opt.label}
                      {recommended && (
                        <span className="ml-1 font-mono text-[0.65rem] font-normal uppercase tracking-[0.08em] text-accent">
                          (recommended)
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-ink-soft">{opt.consequence}</span>
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </fieldset>

      {!resolved && (
        <div className="mt-4">
          <label
            htmlFor={`note-${decision.decision_id}`}
            className="block font-mono text-[0.62rem] uppercase tracking-[0.1em] text-ink-faint"
          >
            Note (draft saved locally)
          </label>
          <textarea
            id={`note-${decision.decision_id}`}
            value={draft}
            onChange={(e) => setDraft(decision.decision_id, e.target.value)}
            rows={2}
            className="mt-1 w-full border border-rule-strong bg-paper px-2 py-1 text-sm"
          />
        </div>
      )}

      {!resolved && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {isRfa ? (
            // Two-step so-ordered ceremony: the first click only ARMS confirmation.
            soOrderStage === "idle" ? (
              <button
                type="button"
                onClick={() => setSoOrderStage("confirm")}
                disabled={mutation.isPending}
                className="border border-accent bg-accent-soft px-3 py-1.5 font-mono text-sm font-bold text-accent hover:bg-accent hover:text-paper"
              >
                So order…
              </button>
            ) : (
              <div className="ink-in flex flex-col gap-2 border border-accent bg-accent-soft p-3">
                <p className="text-sm">
                  <span className="font-bold text-accent">Consequence:</span>{" "}
                  {chosenOption?.consequence ?? "This admission binds the record."}
                </p>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => mutation.mutate("approve")}
                    disabled={mutation.isPending}
                    className="border border-accent bg-accent px-3 py-1.5 font-mono text-sm font-bold text-paper"
                  >
                    {mutation.isPending ? "Entering…" : "Confirm so-ordered"}
                  </button>
                  <button
                    type="button"
                    onClick={() => setSoOrderStage("idle")}
                    disabled={mutation.isPending}
                    className="border border-rule-strong px-3 py-1.5 font-mono text-sm text-ink-soft"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )
          ) : (
            <>
              <ActionButton onClick={() => mutation.mutate("approve")} pending={mutation.isPending}>
                Approve
              </ActionButton>
              <ActionButton onClick={() => mutation.mutate("modify")} pending={mutation.isPending}>
                Modify
              </ActionButton>
              <ActionButton onClick={() => mutation.mutate("deny")} pending={mutation.isPending}>
                Deny
              </ActionButton>
            </>
          )}
        </div>
      )}

      {resolved && decision.resolution && (
        <p className="mt-3 font-mono text-[0.72rem] text-ink-soft">
          {decision.resolution.action} · {decision.resolution.decided_by} ·{" "}
          {decision.resolution.decided_at}
        </p>
      )}

      {error && (
        <p role="alert" aria-live="assertive" className="mt-3 font-mono text-sm text-fail">
          {error}
        </p>
      )}
    </article>
  );
}

function ActionButton({
  children,
  onClick,
  pending,
}: {
  children: React.ReactNode;
  onClick: () => void;
  pending: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={pending}
      className="border border-rule-strong bg-paper px-3 py-1.5 font-mono text-sm text-ink transition-colors hover:border-accent hover:text-accent disabled:opacity-40"
    >
      {children}
    </button>
  );
}
