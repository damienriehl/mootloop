/**
 * Export domain module (FD-8): a run's deliverables and the certify-and-release
 * signed-link mint (FD-9 colophon / P-37). DRAFT files are always linkable; clean
 * `.docx` require the run to be export-ready — the server enforces this and returns a
 * typed 403 (`ExportNotReadyError`, see errors.ts) with its blockers list.
 *
 * NEVER-optimistic on the mint: the caller waits for the server-signed link before
 * reflecting any release in the UI (the link is minted and the download is audit-logged
 * server-side).
 */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type { DeliverablesResponse, SignedLinkResponse } from "./types";

type Ids = { matterId: string; runId: string };

function unwrap<T>(result: { data?: T }): T {
  return result.data as T;
}

/** List a run's deliverables with per-file DRAFT/clean state + the export-ready gate. */
export async function getDeliverables(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<DeliverablesResponse> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/runs/{run_id}/deliverables", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

/**
 * Mint a short-expiry signed download link for one deliverable. Throws
 * `ExportNotReadyError` (typed 403) when a clean file is requested before the run is
 * export-ready. Every download the link resolves to is logged to the access audit.
 */
export async function mintDownloadLink(
  { matterId, runId, name }: Ids & { name: string },
  client: ApiClient = getClient(),
): Promise<SignedLinkResponse> {
  return unwrap(
    await client.POST(
      "/api/matters/{matter_id}/runs/{run_id}/deliverables/{name}/link",
      {
        params: { path: { matter_id: matterId, run_id: runId, name } },
      },
    ),
  );
}
