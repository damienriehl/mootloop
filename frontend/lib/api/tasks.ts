/**
 * Tasks (on-ramp) domain module (FD-8): the freeform lane that turns an attorney's
 * free-text intent into a resolved-or-unmapped TaskSpec slip. Path params ride through
 * openapi-fetch's typed `params.path`, so a wrong param name is a compile error.
 *
 * FE-2.5 ships the FREEFORM lane only; the wizard/suggestion input-shape branches of
 * the FD-9 omnibox land in FE-3.
 */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type { TaskSpecResponse, TaskSpecsResponse } from "./types";

function unwrap<T>(result: { data?: T }): T {
  // The client middleware throws on any non-2xx, so reaching here means `data` exists.
  return result.data as T;
}

/** Every recorded TaskSpec for a matter (append order). */
export async function listTaskSpecs(
  matterId: string,
  client: ApiClient = getClient(),
): Promise<TaskSpecsResponse> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/tasks", {
      params: { path: { matter_id: matterId } },
    }),
  );
}

/**
 * Submit a free-text intent to the freeform on-ramp. Returns the TaskSpec slip plus
 * `runnable` (false when the concept did not resolve to a runnable task adapter —
 * the spec is still recorded for the audit trail, but no run can start from it yet).
 */
export async function createFreeformTask(
  matterId: string,
  intentText: string,
  client: ApiClient = getClient(),
): Promise<TaskSpecResponse> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/tasks/freeform", {
      params: { path: { matter_id: matterId } },
      body: { intent_text: intentText },
    }),
  );
}
