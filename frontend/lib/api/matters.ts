/**
 * Matters domain module (FD-8): typed wrappers over the client for matter listing.
 * Each wrapper unwraps openapi-fetch's `{ data, error }` — the client middleware has
 * already thrown a typed error on failure, so `data` is present on the happy path.
 */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type {
  CloseMatterRequest,
  CloseRecord,
  MatterContextRequest,
  MatterContextResponse,
  MatterSummary,
} from "./types";

function unwrap<T>(result: { data?: T }): T {
  return result.data as T;
}

export async function listMatters(client: ApiClient = getClient()): Promise<MatterSummary[]> {
  const { data } = await client.GET("/api/matters");
  return data ?? [];
}

/** Hard-human matter close; server derives actor, time, and backup destination. */
export async function closeMatter(
  matterId: string,
  body: CloseMatterRequest,
  client: ApiClient = getClient(),
): Promise<CloseRecord> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/close", {
      params: { path: { matter_id: matterId } },
      body,
    }),
  );
}

export async function getMatterContext(
  matterId: string,
  client: ApiClient = getClient(),
): Promise<MatterContextResponse | null> {
  const { data } = await client.GET("/api/matters/{matter_id}/context", {
    params: { path: { matter_id: matterId } },
  });
  return data ?? null;
}

/** Human-approved matter memory; the server derives actor and time. */
export async function setMatterContext(
  matterId: string,
  body: MatterContextRequest,
  client: ApiClient = getClient(),
): Promise<MatterContextResponse> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/context", {
      params: { path: { matter_id: matterId } },
      body,
    }),
  );
}
