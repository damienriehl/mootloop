"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  getProductionSuggestions,
  queueProductionSuggestions,
  reviewProductionSuggestion,
} from "@/lib/api/production";
import { keys } from "@/lib/api/keys";
import type {
  ProductionDisposition,
  ProductionReviewAction,
  ProductionSuggestionView,
} from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";

export function ProductionReviewQueue({ matterId, runId }: { matterId: string; runId: string }) {
  const client = useQueryClient();
  const queryKey = keys.matter(matterId).run(runId).productionSuggestions();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey,
    queryFn: () => getProductionSuggestions({ matterId, runId }),
  });
  const suggestions = data?.suggestions ?? [];
  const selected = suggestions.find((item) => item.suggestion_id === selectedId) ?? suggestions[0];

  const generate = useMutation({
    mutationFn: () => queueProductionSuggestions({ matterId, runId }),
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onSuccess: () => setNotice("RFP suggestions queued. Refresh after the worker completes."),
    onError: (err) => setError((err as Error).message),
    onSettled: () => client.invalidateQueries({ queryKey }),
  });

  const review = useMutation({
    mutationFn: ({
      action,
      disposition,
    }: {
      action: ProductionReviewAction;
      disposition?: ProductionDisposition;
    }) =>
      reviewProductionSuggestion({
        matterId,
        runId,
        suggestionId: selected!.suggestion_id,
        action,
        disposition,
      }),
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onSuccess: ({ suggestion }) => {
      client.setQueryData(queryKey, (current: typeof data) =>
        current
          ? {
              ...current,
              suggestions: (current.suggestions ?? []).map((item) =>
                item.suggestion_id === suggestion.suggestion_id ? suggestion : item,
              ),
            }
          : current,
      );
      setNotice("Human review recorded.");
    },
    onError: (err) => setError((err as Error).message),
  });

  return (
    <section className="border border-rule bg-paper-raised p-4 shadow-ledger">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="font-mono text-[0.7rem] font-bold uppercase tracking-[0.12em] text-ink">
            RFP production review
          </h2>
          <p className="mt-1 text-sm text-ink-soft">
            Classifications are suggestions only. Accepting one never authorizes production.
          </p>
        </div>
        <button
          type="button"
          disabled={generate.isPending || data?.eligible === false}
          onClick={() => generate.mutate()}
          className="border border-rule-strong bg-paper px-3 py-1.5 font-mono text-sm hover:border-accent hover:text-accent disabled:opacity-50"
        >
          {generate.isPending ? "Queueing…" : "Generate suggestions"}
        </button>
      </div>

      {data?.eligible === false ? (
        <p className="mt-4 font-mono text-sm text-ink-faint">
          This run has no requests for production, so there are no document suggestions to
          generate.
        </p>
      ) : suggestions.length > 0 ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(14rem,0.8fr)_minmax(0,1.4fr)]">
          <div className="grid content-start gap-1" aria-label="Production suggestions">
            {suggestions.map((item) => (
              <button
                key={item.suggestion_id}
                type="button"
                onClick={() => setSelectedId(item.suggestion_id)}
                className={cn(
                  "border px-3 py-2 text-left",
                  item.suggestion_id === selected?.suggestion_id
                    ? "border-accent bg-paper text-accent"
                    : "border-rule bg-paper hover:border-rule-strong",
                )}
              >
                <span className="block font-mono text-xs font-bold">{item.request_id}</span>
                <span className="block truncate text-sm">{item.original_name}</span>
                <span className="font-mono text-[0.65rem] uppercase text-ink-faint">
                  {item.classification.replace("_", " ")} · {item.review_status.replace("_", " ")}
                </span>
              </button>
            ))}
          </div>
          {selected ? <SuggestionDetail item={selected} busy={review.isPending} review={review.mutate} /> : null}
        </div>
      ) : (
        <p className="mt-4 font-mono text-sm text-ink-faint">No suggestions generated yet.</p>
      )}

      {(data?.exclusions?.length ?? 0) > 0 ? (
        <p className="mt-3 font-mono text-xs text-ink-faint">
          {data?.exclusions?.length} privileged, untriaged, or unavailable document/request pair(s)
          excluded before classification.
        </p>
      ) : null}
      {error ? <p role="alert" className="mt-3 font-mono text-sm text-fail">{error}</p> : null}
      {notice ? <p role="status" className="mt-3 font-mono text-sm text-pass">{notice}</p> : null}
    </section>
  );
}

function SuggestionDetail({
  item,
  busy,
  review,
}: {
  item: ProductionSuggestionView;
  busy: boolean;
  review: (value: { action: ProductionReviewAction; disposition?: ProductionDisposition }) => void;
}) {
  return (
    <div className="border border-rule bg-paper p-4">
      <div className="flex flex-wrap justify-between gap-2">
        <div>
          <p className="font-mono text-xs font-bold text-accent">{item.request_id}</p>
          <h3 className="mt-1 font-semibold text-ink">{item.original_name}</h3>
        </div>
        <span className="font-mono text-xs uppercase text-ink-soft">
          {item.classification.replace("_", " ")} · {(item.score * 100).toFixed(0)}%
        </span>
      </div>
      <p className="mt-3 text-sm text-ink-soft">{item.reason}</p>
      <dl className="mt-3 grid gap-1 font-mono text-[0.65rem] text-ink-faint">
        <div><dt className="inline">Source: </dt><dd className="inline">{item.source_locator}</dd></div>
        <div><dt className="inline">Document SHA: </dt><dd className="inline break-all">{item.document_sha256}</dd></div>
      </dl>
      <div className="mt-4 flex flex-wrap gap-2">
        <ReviewButton disabled={busy} onClick={() => review({ action: "accept" })}>Accept classification</ReviewButton>
        <ReviewButton disabled={busy} onClick={() => review({ action: "reject" })}>Reject classification</ReviewButton>
      </div>
      <div className="mt-3 border-t border-rule pt-3">
        <p className="mb-2 font-mono text-[0.65rem] uppercase text-ink-faint">
          Separate attorney production decision
        </p>
        <div className="flex flex-wrap gap-2">
          {(["produce", "withhold", "defer"] as const).map((disposition) => (
            <ReviewButton
              key={disposition}
              disabled={busy}
              onClick={() => review({ action: "production_review", disposition })}
            >
              {disposition}
            </ReviewButton>
          ))}
        </div>
        <p className="mt-2 font-mono text-xs text-ink-soft">
          Recorded: {item.production_disposition ?? "no production decision"}
        </p>
      </div>
    </div>
  );
}

function ReviewButton({ children, disabled, onClick }: { children: React.ReactNode; disabled: boolean; onClick: () => void }) {
  return (
    <button type="button" disabled={disabled} onClick={onClick} className="border border-rule-strong px-2.5 py-1 font-mono text-xs hover:border-accent hover:text-accent disabled:opacity-50">
      {children}
    </button>
  );
}
