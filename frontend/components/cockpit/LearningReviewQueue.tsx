"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import {
  acceptLearning,
  getLearningProposals,
  importLearningDocx,
  previewLearningScrub,
  promoteLearning,
  rejectLearning,
} from "@/lib/api/learnings";
import { keys } from "@/lib/api/keys";
import type { LearningProposalView } from "@/lib/api/types";
import { cn } from "@/lib/utils/cn";

const MAX_DOCX_BYTES = 64 * 1024 * 1024;

function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let start = 0; start < bytes.length; start += 32_768) {
    binary += String.fromCharCode(...bytes.subarray(start, start + 32_768));
  }
  return btoa(binary);
}

export function LearningReviewQueue({ matterId, runId }: { matterId: string; runId: string }) {
  const queryClient = useQueryClient();
  const queryKey = keys.matter(matterId).learnings();
  const [file, setFile] = useState<File | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { data } = useQuery({
    queryKey,
    queryFn: () => getLearningProposals(matterId),
  });
  const proposals = useMemo(
    () => (data?.proposals ?? []).filter((item) => item.run_id === runId),
    [data?.proposals, runId],
  );
  const blockedImports = useMemo(
    () => (data?.imports ?? []).filter((item) => item.run_id === runId && !item.auto_routable),
    [data?.imports, runId],
  );
  const selected = proposals.find((item) => item.proposal_id === selectedId) ?? proposals[0];

  const refresh = async () => queryClient.invalidateQueries({ queryKey });
  const upload = useMutation({
    mutationFn: async () => {
      if (!file) throw new Error("Choose an attorney-edited DOCX first.");
      if (file.size > MAX_DOCX_BYTES) throw new Error("DOCX exceeds the 64 MiB import limit.");
      return importLearningDocx({
        matterId,
        runId,
        sourceName: file.name,
        sourceBase64: toBase64(await file.arrayBuffer()),
      });
    },
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onSuccess: (result) => {
      setNotice(
        result.import_record.auto_routable
          ? `${result.proposals?.length ?? 0} anchored learning proposal(s) created.`
          : `Import needs human anchor review: ${result.import_record.blockers.join("; ")}`,
      );
      setFile(null);
    },
    onError: (err) => setError((err as Error).message),
    onSettled: refresh,
  });

  return (
    <section className="border border-rule bg-paper-raised p-4 shadow-ledger">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="max-w-2xl">
          <h2 className="font-mono text-[0.7rem] font-bold uppercase tracking-[0.12em] text-ink">
            Edit-learning review
          </h2>
          <p className="mt-1 text-sm text-ink-soft">
            Import an attorney-edited DOCX. Recovered changes remain proposals until a human
            accepts them; accepted learning affects the next run, never this immutable run.
          </p>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <label className="border border-rule-strong bg-paper px-3 py-1.5 font-mono text-xs hover:border-accent">
            <span>{file ? file.name : "Choose edited DOCX"}</span>
            <input
              className="sr-only"
              type="file"
              accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <button
            type="button"
            disabled={!file || upload.isPending}
            onClick={() => upload.mutate()}
            className="border border-rule-strong bg-paper px-3 py-1.5 font-mono text-xs hover:border-accent hover:text-accent disabled:opacity-50"
          >
            {upload.isPending ? "Importing…" : "Import edits"}
          </button>
        </div>
      </div>

      {blockedImports.length > 0 ? (
        <div className="mt-4 border border-accent bg-paper-raised p-3" aria-label="Blocked DOCX imports">
          <p className="font-mono text-[0.65rem] font-bold uppercase tracking-[0.08em] text-accent">
            Needs human anchor review
          </p>
          {blockedImports.map((item) => (
            <div key={item.import_id} className="mt-2 text-sm text-ink-soft">
              <span className="font-mono text-xs text-ink">{item.source_name}</span>
              <span className="block">{item.blockers.join("; ")}</span>
            </div>
          ))}
        </div>
      ) : null}

      {proposals.length > 0 ? (
        <div className="mt-4 grid gap-4 lg:grid-cols-[minmax(14rem,0.75fr)_minmax(0,1.5fr)]">
          <div className="grid content-start gap-1" aria-label="Learning proposals">
            {proposals.map((item) => (
              <button
                key={item.proposal_id}
                type="button"
                onClick={() => setSelectedId(item.proposal_id)}
                className={cn(
                  "border px-3 py-2 text-left",
                  item.proposal_id === selected?.proposal_id
                    ? "border-accent bg-paper text-accent"
                    : "border-rule bg-paper hover:border-rule-strong",
                )}
              >
                <span className="block font-mono text-xs font-bold">{item.anchor_id}</span>
                <span className="block truncate text-sm">{item.word_changes} word change(s)</span>
                <span className="font-mono text-[0.65rem] uppercase text-ink-faint">
                  {item.status.replace("_", " ")} · {(item.active_tiers ?? []).join(" + ") || "unrouted"}
                </span>
              </button>
            ))}
          </div>
          {selected ? (
            <LearningDetail
              key={`${selected.proposal_id}:${selected.review_history?.length ?? 0}`}
              item={selected}
              matterId={matterId}
              refresh={refresh}
              setError={setError}
              setNotice={setNotice}
            />
          ) : null}
        </div>
      ) : (
        <p className="mt-4 font-mono text-sm text-ink-faint">
          No edit-derived learning proposals for this run.
        </p>
      )}
      {error ? <p role="alert" className="mt-3 font-mono text-sm text-fail">{error}</p> : null}
      {notice ? <p role="status" className="mt-3 font-mono text-sm text-pass">{notice}</p> : null}
    </section>
  );
}

function LearningDetail({
  item,
  matterId,
  refresh,
  setError,
  setNotice,
}: {
  item: LearningProposalView;
  matterId: string;
  refresh: () => Promise<unknown>;
  setError: (value: string | null) => void;
  setNotice: (value: string | null) => void;
}) {
  const [text, setText] = useState(item.accepted_text ?? item.proposed_text);
  const [scrubDiff, setScrubDiff] = useState<string | null>(null);
  const [scrubHash, setScrubHash] = useState<string | null>(null);
  const [confirmPublic, setConfirmPublic] = useState(false);
  const [excludedMatters, setExcludedMatters] = useState("");
  const action = useMutation({
    mutationFn: async (kind: "accept" | "reject" | "scrub" | "firm" | "area") => {
      if (kind === "accept") return acceptLearning(matterId, item.proposal_id, text);
      if (kind === "reject") return rejectLearning(matterId, item.proposal_id, "Not reusable");
      if (kind === "scrub") return previewLearningScrub(matterId, item.proposal_id, text);
      return promoteLearning(
        matterId,
        item.proposal_id,
        kind,
        text,
        kind === "area" && confirmPublic,
        scrubHash ?? "",
        excludedMatters
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
      );
    },
    onMutate: () => {
      setError(null);
      setNotice(null);
    },
    onSuccess: (result, kind) => {
      if (kind === "scrub" && "rendered_diff" in result) {
        setScrubDiff(result.rendered_diff);
        setScrubHash(result.rendered_diff_sha256);
        setNotice("Sharing scrub passed. Review the rendered diff before promotion.");
      } else {
        setNotice(kind === "accept" ? "Matter learning accepted for the next run." : "Human review recorded.");
      }
    },
    onError: (err) => setError((err as Error).message),
    onSettled: refresh,
  });

  return (
    <div className="border border-rule bg-paper p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="font-mono text-sm font-bold text-accent">{item.anchor_id}</h3>
        <span className="font-mono text-[0.65rem] uppercase text-ink-faint">
          {item.status.replace("_", " ")}
        </span>
      </div>
      <p className="mt-3 font-mono text-[0.65rem] uppercase tracking-[0.08em] text-ink-faint">
        Recovered word diff
      </p>
      <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap border border-rule bg-paper-raised p-3 font-mono text-xs text-ink-soft">
        {item.critic_markup}
      </pre>
      <label className="mt-3 block font-mono text-[0.65rem] uppercase text-ink-faint">
        Attorney-reviewed learning text
        <textarea
          value={text}
          disabled={item.status === "rejected"}
          onChange={(event) => {
            setText(event.target.value);
            setScrubDiff(null);
            setScrubHash(null);
            setConfirmPublic(false);
          }}
          className="mt-1 min-h-24 w-full border border-rule-strong bg-paper p-2 font-sans text-sm normal-case text-ink disabled:opacity-60"
        />
      </label>

      {item.status === "needs_review" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <ActionButton disabled={action.isPending || !text.trim()} onClick={() => action.mutate("accept")}>
            Accept for matter
          </ActionButton>
          <ActionButton disabled={action.isPending} onClick={() => action.mutate("reject")}>
            Reject
          </ActionButton>
        </div>
      ) : item.status === "accepted" ? (
        <div className="mt-3 border-t border-rule pt-3">
          <p className="font-mono text-[0.65rem] uppercase text-ink-faint">
            Optional shared promotion — separate human act
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <ActionButton disabled={action.isPending || !text.trim()} onClick={() => action.mutate("scrub")}>
              Preview sharing scrub
            </ActionButton>
            <ActionButton disabled={action.isPending || !scrubDiff} onClick={() => action.mutate("firm")}>
              Promote to firm
            </ActionButton>
          </div>
          {scrubDiff ? (
            <div className="mt-3">
              <label className="block font-mono text-[0.65rem] uppercase text-ink-faint">
                Ethical-wall exclusions (comma-separated matter IDs)
                <input
                  value={excludedMatters}
                  onChange={(event) => setExcludedMatters(event.target.value)}
                  className="mt-1 w-full border border-rule-strong bg-paper p-2 font-sans text-sm normal-case text-ink"
                  placeholder="2026-01-02-client-matter"
                />
              </label>
              <p className="font-mono text-[0.65rem] uppercase text-ink-faint">Rendered scrub diff</p>
              <pre className="mt-1 whitespace-pre-wrap border border-rule bg-paper-raised p-3 font-mono text-xs text-ink-soft">
                {scrubDiff}
              </pre>
              <label className="mt-2 flex items-start gap-2 text-sm text-ink-soft">
                <input
                  type="checkbox"
                  checked={confirmPublic}
                  onChange={(event) => setConfirmPublic(event.target.checked)}
                />
                I reviewed this exact scrub diff. Stage an area-playbook candidate; do not write or
                commit the public repository.
              </label>
              <ActionButton disabled={action.isPending || !confirmPublic} onClick={() => action.mutate("area")}>
                Stage area candidate
              </ActionButton>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function ActionButton({
  children,
  disabled,
  onClick,
}: {
  children: React.ReactNode;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="border border-rule-strong px-2.5 py-1 font-mono text-xs hover:border-accent hover:text-accent disabled:opacity-50"
    >
      {children}
    </button>
  );
}
