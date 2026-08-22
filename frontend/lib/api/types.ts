/**
 * Convenience aliases over the generated OpenAPI component schemas (FD-8).
 * These are the single source of domain truth — no hand-mirrored types.
 */
import type { components } from "./schema";

type Schemas = components["schemas"];

export type MatterSummary = Schemas["MatterSummary"];
export type CloseMatterRequest = Schemas["CloseMatterRequest"];
export type CloseRecord = Schemas["CloseRecord"];
export type MatterContextRequest = Schemas["MatterContextRequest"];
export type MatterContextResponse = Schemas["MatterContextResponse"];
export type RunSummary = Schemas["RunSummary"];
export type RunStatusSummary = Schemas["RunStatusSummary"];
export type AttentionBlocker = Schemas["AttentionBlocker"];
export type TraceTree = Schemas["TraceTree"];
export type RunEvidencePack = Schemas["RunEvidencePack"];
export type RunActionResponse = Schemas["RunActionResponse"];
export type CitationCheckQueuedResponse = Schemas["CitationCheckQueuedResponse"];
export type JudgeProfileQueuedResponse = Schemas["JudgeProfileQueuedResponse"];
export type ProductionSuggestionView = Schemas["ProductionSuggestionView"];
export type ProductionSuggestionResponse = Schemas["ProductionSuggestionResponse"];
export type ProductionSuggestionsResponse = Schemas["ProductionSuggestionsResponse"];
export type ProductionSuggestionsQueuedResponse = Schemas["ProductionSuggestionsQueuedResponse"];
export type ProductionSuggestionReviewRequest = Schemas["ProductionSuggestionReviewRequest"];
export type LearningImportResponse = Schemas["LearningImportResponse"];
export type LearningProposalsResponse = Schemas["LearningProposalsResponse"];
export type LearningProposalResponse = Schemas["LearningProposalResponse"];
export type LearningProposalView = Schemas["LearningProposalView"];
export type LearningScrubResponse = Schemas["LearningScrubResponse"];
export type LearningTier = Schemas["LearningPromotionRequest"]["target_tier"];
export type GateLedgerResponse = Schemas["GateLedgerResponse"];
export type DecisionsResponse = Schemas["DecisionsResponse"];
export type RequestsResponse = Schemas["RequestsResponse"];
export type Decision = Schemas["Decision"];
export type DecisionKind = Schemas["DecisionKind"];
export type DecisionOption = Schemas["DecisionOption"];
export type DecisionProposal = Schemas["DecisionProposal"];
export type DecisionResolution = Schemas["DecisionResolution"];
export type ResolveRequest = Schemas["ResolveRequest"];
export type ResolveResponse = Schemas["ResolveResponse"];
export type Attestation = Schemas["CurrentAttestation"];
export type AttestResponse = Schemas["AttestResponse"];
export type ReviewIntegrityStatus = Schemas["ReviewIntegrityStatus"];
export type StartRunRequest = Schemas["StartRunRequest"];
export type RaiseCapRequest = Schemas["RaiseCapRequest"];
export type ReopenRunRequest = Schemas["ReopenRunRequest"];
export type RequestItem = Schemas["RequestItem"];
export type GatePass = Schemas["GatePass"];
export type GateFail = Schemas["GateFail"];
export type GatePending = Schemas["GatePending"];
export type GateResult = GatePass | GateFail | GatePending;

// --- On-ramp (FE-2.5): the freeform lane produces a TaskSpec slip ---
export type TaskSpec = Schemas["TaskSpec"];
export type TaskSpecLock = Schemas["TaskSpecLock"];
export type TaskSpecLockResponse = Schemas["TaskSpecLockResponse"];
export type TaskSpecResponse = Schemas["TaskSpecResponse"];
export type TaskSpecsResponse = Schemas["TaskSpecsResponse"];
export type FreeformTaskRequest = Schemas["FreeformTaskRequest"];
/** The on-ramp lane that produced a spec (freeform is the only live lane in FE-2.5). */
export type SourceLane = TaskSpec["source_lane"];

// --- Export room (FE-2.5): deliverables + certify-and-release signed links ---
export type DeliverableInfo = Schemas["DeliverableInfo"];
export type DeliverablesResponse = Schemas["DeliverablesResponse"];
export type SignedLinkResponse = Schemas["SignedLinkResponse"];

/** Run lifecycle status (the FastAPI `RunStatus` Literal). */
export type RunStatus = RunStatusSummary["status"];
/** Run execution mode. */
export type RunMode = RunStatusSummary["mode"];
/** Resolution action for a decision. */
export type ResolutionAction = ResolveRequest["action"];
export type ProductionReviewAction = ProductionSuggestionReviewRequest["action"];
export type ProductionDisposition = NonNullable<
  ProductionSuggestionReviewRequest["production_disposition"]
>;

/** The terminal statuses — a run in one of these no longer ticks (FD-5). */
export const TERMINAL_STATUSES: readonly RunStatus[] = ["finished", "needs_attention", "capped"];

export function isTerminalStatus(status: RunStatus): boolean {
  return TERMINAL_STATUSES.includes(status);
}
