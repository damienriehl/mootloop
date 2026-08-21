/** Typed U-09 edited-DOCX and reviewed-learning API wrappers. */
import type { ApiClient } from "./client";
import { getClient } from "./client";
import type {
  LearningImportResponse,
  LearningProposalResponse,
  LearningProposalsResponse,
  LearningScrubResponse,
  LearningTier,
} from "./types";

function unwrap<T>(result: { data?: T }): T {
  return result.data as T;
}

export async function importLearningDocx(
  {
    matterId,
    runId,
    sourceName,
    sourceBase64,
  }: { matterId: string; runId: string; sourceName: string; sourceBase64: string },
  client: ApiClient = getClient(),
): Promise<LearningImportResponse> {
  return unwrap(
    await client.POST("/api/matters/{matter_id}/runs/{run_id}/learnings/import", {
      params: { path: { matter_id: matterId, run_id: runId } },
      body: { source_name: sourceName, source_base64: sourceBase64 },
    }),
  );
}

export async function getLearningProposals(
  matterId: string,
  client: ApiClient = getClient(),
): Promise<LearningProposalsResponse> {
  return unwrap(
    await client.GET("/api/matters/{matter_id}/learnings/proposals", {
      params: { path: { matter_id: matterId } },
    }),
  );
}

async function review(
  matterId: string,
  proposalId: string,
  action: "accept" | "reject",
  body: { reviewed_text: string; reason: string },
  client: ApiClient,
): Promise<LearningProposalResponse> {
  const path =
    action === "accept"
      ? "/api/matters/{matter_id}/learnings/proposals/{proposal_id}/accept"
      : "/api/matters/{matter_id}/learnings/proposals/{proposal_id}/reject";
  return unwrap(
    await client.POST(path, {
      params: { path: { matter_id: matterId, proposal_id: proposalId } },
      body,
    }),
  );
}

export function acceptLearning(
  matterId: string,
  proposalId: string,
  reviewedText: string,
  client: ApiClient = getClient(),
): Promise<LearningProposalResponse> {
  return review(
    matterId,
    proposalId,
    "accept",
    { reviewed_text: reviewedText, reason: "" },
    client,
  );
}

export function rejectLearning(
  matterId: string,
  proposalId: string,
  reason: string,
  client: ApiClient = getClient(),
): Promise<LearningProposalResponse> {
  return review(
    matterId,
    proposalId,
    "reject",
    { reviewed_text: "", reason },
    client,
  );
}

export async function previewLearningScrub(
  matterId: string,
  proposalId: string,
  reviewedText: string,
  client: ApiClient = getClient(),
): Promise<LearningScrubResponse> {
  return unwrap(
    await client.POST(
      "/api/matters/{matter_id}/learnings/proposals/{proposal_id}/scrub",
      {
        params: { path: { matter_id: matterId, proposal_id: proposalId } },
        body: { reviewed_text: reviewedText },
      },
    ),
  );
}

export async function promoteLearning(
  matterId: string,
  proposalId: string,
  targetTier: Exclude<LearningTier, "matter">,
  reviewedText: string,
  confirmScrubDiff: boolean,
  scrubDiffSha256: string,
  excludedMatterIds: string[] = [],
  client: ApiClient = getClient(),
): Promise<LearningProposalResponse> {
  return unwrap(
    await client.POST(
      "/api/matters/{matter_id}/learnings/proposals/{proposal_id}/promote",
      {
        params: { path: { matter_id: matterId, proposal_id: proposalId } },
        body: {
          target_tier: targetTier,
          reviewed_text: reviewedText,
          confirm_scrub_diff: confirmScrubDiff,
          scrub_diff_sha256: scrubDiffSha256,
          excluded_matter_ids: excludedMatterIds,
        },
      },
    ),
  );
}
