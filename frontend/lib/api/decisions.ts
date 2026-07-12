/**
 * Decisions domain module (FD-8): decision reads, the (optimistic-capable) resolve,
 * and the never-optimistic attest. `decided_by`/timestamp are server-derived from the
 * verified Access principal — never sent from the client.
 */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type {
  Attestation,
  DecisionsResponse,
  ResolutionAction,
  ResolveResponse,
} from "./types";

type Ids = { matterId: string; runId: string };

function unwrap<T>(result: { data?: T }): T {
  return result.data as T;
}

export async function getDecisions(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<DecisionsResponse> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/runs/{run_id}/decisions", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export interface ResolveArgs extends Ids {
  decisionId: string;
  action: ResolutionAction;
  chosenKey?: string | null;
  note?: string;
}

export async function resolveDecision(
  { matterId, runId, decisionId, action, chosenKey, note }: ResolveArgs,
  client: ApiClient = getClient(),
): Promise<ResolveResponse> {
  return unwrap(
    await client.POST(
      "/api/matters/{matter_id}/runs/{run_id}/decisions/{decision_id}/resolve",
      {
        params: { path: { matter_id: matterId, run_id: runId, decision_id: decisionId } },
        body: { action, chosen_key: chosenKey ?? null, note: note ?? "" },
      },
    ),
  );
}

/**
 * Attest a run. This is a DISTINCT, deliberate act and MUST NEVER be optimistic
 * (FD-8/FD-9) — the caller waits for this promise before reflecting any UI change.
 */
export async function attestRun(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<Attestation> {
  const res = await client.POST("/api/matters/{matter_id}/runs/{run_id}/attest", {
    params: { path: { matter_id: matterId, run_id: runId } },
  });
  return unwrap(res).attestation;
}
