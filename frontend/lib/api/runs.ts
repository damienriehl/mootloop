/**
 * Runs domain module (FD-8): typed wrappers over the run lifecycle + read endpoints.
 * Path params are threaded through openapi-fetch's typed `params.path`, so a wrong
 * param name is a compile error, not a 404.
 */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type {
  GateLedgerResponse,
  RaiseCapRequest,
  RequestsResponse,
  RunActionResponse,
  RunStatusSummary,
  RunSummary,
  StartRunRequest,
} from "./types";

type Ids = { matterId: string; runId: string };

function unwrap<T>(result: { data?: T }): T {
  // The client middleware throws on any non-2xx, so reaching here means `data` exists.
  return result.data as T;
}

export async function listRuns(matterId: string, client: ApiClient = getClient()): Promise<RunSummary[]> {
  const { data } = await client.GET("/api/matters/{matter_id}/runs", {
    params: { path: { matter_id: matterId } },
  });
  return data ?? [];
}

export async function getRun(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<RunStatusSummary> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/runs/{run_id}", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export async function getRunGates(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<GateLedgerResponse> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/runs/{run_id}/gates", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export async function getRunRequests(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<RequestsResponse> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/runs/{run_id}/requests", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export async function startRun(
  matterId: string,
  body: StartRunRequest,
  client: ApiClient = getClient(),
): Promise<RunStatusSummary> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/runs", {
      params: { path: { matter_id: matterId } },
      body,
    }),
  );
}

export async function continueRun(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<RunActionResponse> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/runs/{run_id}/continue", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export async function pauseRun(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<RunActionResponse> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/runs/{run_id}/pause", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export async function resumeRun(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<RunActionResponse> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/runs/{run_id}/resume", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export async function raiseCap(
  { matterId, runId }: Ids,
  body: RaiseCapRequest,
  client: ApiClient = getClient(),
): Promise<RunActionResponse> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/runs/{run_id}/raise-cap", {
      params: { path: { matter_id: matterId, run_id: runId } },
      body,
    }),
  );
}
