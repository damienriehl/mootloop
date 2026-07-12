"use client";

/**
 * `useRunStream` (FD-8): subscribe to a run's SSE journal and fold it into the
 * TanStack Query cache — the cache is the single source of truth, no useState mirror.
 *
 * - `@microsoft/fetch-event-source` so we get `credentials: "include"` + a real content
 *   type on `onopen` (native EventSource can't detect the Access login redirect).
 * - Heartbeats (`:` comment frames) never reach `onmessage`; blank payloads are ignored.
 * - An HTML `onopen` body means Access served a login page → `SessionExpiredError` →
 *   redirect to re-auth (the deep link is preserved by Access).
 * - Each valid event is parsed by zod and reduced into the stream-state cache entry;
 *   decision/gate events also invalidate their read queries so the rooms refresh.
 */
import { fetchEventSource } from "@microsoft/fetch-event-source";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { parseRunEvent, type RunEvent } from "./events";
import { keys } from "./keys";
import {
  initialRunStreamState,
  reduceRunEvent,
  type RunStreamState,
} from "./runStream";

export function streamKey(matterId: string, runId: string) {
  return [...keys.matter(matterId).run(runId).key, "stream"] as const;
}

class StreamSessionExpired extends Error {}

function applyEvent(
  client: QueryClient,
  matterId: string,
  runId: string,
  event: RunEvent,
): void {
  const key = streamKey(matterId, runId);
  client.setQueryData<RunStreamState>(key, (prev) =>
    reduceRunEvent(prev ?? initialRunStreamState(), event),
  );
  // Refresh the dependent read views when their underlying data changed.
  const runKeys = keys.matter(matterId).run(runId);
  if (event.kind === "decision_recorded") {
    void client.invalidateQueries({ queryKey: runKeys.decisions() });
  } else if (event.kind === "gate_evaluated" || event.kind === "turn_completed") {
    void client.invalidateQueries({ queryKey: runKeys.gates() });
  }
  if (
    event.kind === "run_finished" ||
    event.kind === "run_paused" ||
    event.kind === "run_resumed" ||
    event.kind === "cap_raised"
  ) {
    void client.invalidateQueries({ queryKey: runKeys.detail() });
  }
}

export interface UseRunStreamOptions {
  matterId: string;
  runId: string;
  enabled?: boolean;
  onSessionExpired?: () => void;
}

export function useRunStream({
  matterId,
  runId,
  enabled = true,
  onSessionExpired,
}: UseRunStreamOptions): RunStreamState {
  const client = useQueryClient();
  const key = streamKey(matterId, runId);

  const { data } = useQuery<RunStreamState>({
    queryKey: key,
    queryFn: () => initialRunStreamState(),
    staleTime: Infinity,
    gcTime: Infinity,
    enabled,
  });

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    const url = `/api/matters/${encodeURIComponent(matterId)}/runs/${encodeURIComponent(runId)}/stream`;

    void fetchEventSource(url, {
      signal: controller.signal,
      credentials: "include",
      openWhenHidden: true,
      async onopen(response) {
        const contentType = response.headers.get("content-type") ?? "";
        if (contentType.includes("text/html")) {
          throw new StreamSessionExpired();
        }
        if (!response.ok || !contentType.includes("text/event-stream")) {
          throw new Error(`stream failed: ${response.status}`);
        }
      },
      onmessage(msg) {
        const parsed = parseRunEvent(msg.data);
        if (parsed.type === "event") {
          applyEvent(client, matterId, runId, parsed.event);
        }
        // heartbeat / invalid frames are dropped (trust boundary).
      },
      onerror(err) {
        if (err instanceof StreamSessionExpired) {
          if (onSessionExpired) onSessionExpired();
          else if (typeof window !== "undefined") window.location.assign(window.location.href);
          throw err; // stop retrying — we are navigating away.
        }
        // Any other error: let fetch-event-source apply its default backoff/retry.
      },
    });

    return () => controller.abort();
  }, [client, matterId, runId, enabled, onSessionExpired, key.join("/")]); // eslint-disable-line react-hooks/exhaustive-deps

  return data ?? initialRunStreamState();
}
