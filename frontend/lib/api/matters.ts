/**
 * Matters domain module (FD-8): typed wrappers over the client for matter listing.
 * Each wrapper unwraps openapi-fetch's `{ data, error }` — the client middleware has
 * already thrown a typed error on failure, so `data` is present on the happy path.
 */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type { MatterSummary } from "./types";

export async function listMatters(client: ApiClient = getClient()): Promise<MatterSummary[]> {
  const { data } = await client.GET("/api/matters");
  return data ?? [];
}
