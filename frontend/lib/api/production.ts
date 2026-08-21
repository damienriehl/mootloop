/** Typed U-08 RFP production-suggestion API wrappers. */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type {
  ProductionDisposition,
  ProductionReviewAction,
  ProductionSuggestionResponse,
  ProductionSuggestionsQueuedResponse,
  ProductionSuggestionsResponse,
} from "./types";

type Ids = { matterId: string; runId: string };

function unwrap<T>(result: { data?: T }): T {
  return result.data as T;
}

export async function getProductionSuggestions(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<ProductionSuggestionsResponse> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/runs/{run_id}/production/suggestions", {
      params: { path: { matter_id: matterId, run_id: runId } },
    }),
  );
}

export async function queueProductionSuggestions(
  { matterId, runId }: Ids,
  client: ApiClient = getClient(),
): Promise<ProductionSuggestionsQueuedResponse> {
  return unwrap(
    await client.POST(
      "/api/matters/{matter_id}/runs/{run_id}/production/suggestions/generate",
      { params: { path: { matter_id: matterId, run_id: runId } } },
    ),
  );
}

export async function reviewProductionSuggestion(
  {
    matterId,
    runId,
    suggestionId,
    action,
    disposition,
    reason,
  }: Ids & {
    suggestionId: string;
    action: ProductionReviewAction;
    disposition?: ProductionDisposition;
    reason?: string;
  },
  client: ApiClient = getClient(),
): Promise<ProductionSuggestionResponse> {
  return unwrap(
    await client.POST(
      "/api/matters/{matter_id}/runs/{run_id}/production/suggestions/{suggestion_id}/review",
      {
        params: {
          path: { matter_id: matterId, run_id: runId, suggestion_id: suggestionId },
        },
        body: {
          action,
          production_disposition: disposition ?? null,
          reason: reason ?? "",
        },
      },
    ),
  );
}
