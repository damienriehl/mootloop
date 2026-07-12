/**
 * Zod schemas at the SSE trust boundary (FD-8).
 *
 * The `/stream` endpoint tails the run journal and emits each `JournalEvent`
 * (`src/mootloop/models/events.py`) as one SSE `data:` frame — a discriminated union
 * on `kind`. SSE is NOT part of the OpenAPI contract (it's `text/event-stream`), so
 * zod is the schema of record here: every frame is parsed before it touches the cache.
 * Heartbeats are `:` comment frames the parser drops upstream, but `parseRunEvent`
 * also treats blank/`keep-alive` payloads as heartbeats defensively.
 */
import { z } from "zod";

const gateFinding = z.object({
  code: z.string(),
  message: z.string(),
  locator: z.string().nullable().optional(),
});

const gateResult = z.discriminatedUnion("status", [
  z.object({ status: z.literal("pass"), gate: z.string(), findings: z.array(gateFinding).default([]) }),
  z.object({ status: z.literal("fail"), gate: z.string(), findings: z.array(gateFinding).default([]) }),
  z.object({ status: z.literal("pending"), gate: z.string(), findings: z.array(gateFinding).default([]) }),
]);

const turnSpec = z.object({
  turn_id: z.string(),
  run_id: z.string(),
  persona: z.string(),
  request_id: z.string().nullable().optional(),
  stage: z.string(),
  output_schema_name: z.string(),
  attempt: z.number().int().default(1),
  prompt_context: z.record(z.string(), z.unknown()).default({}),
});

const turnRecord = z.object({
  spec: turnSpec,
  output: z.record(z.string(), z.unknown()),
  gate_results: z.array(gateResult).default([]),
  completed_at: z.string(),
});

const runStatus = z.enum([
  "running",
  "finished",
  "needs_attention",
  "capped",
  "needs_decisions",
  "checkpoint",
  "paused",
]);

const runMode = z.enum(["autonomous", "gated", "observed"]);
const billingMode = z.enum(["subscription", "api"]);

export const runEventSchema = z.discriminatedUnion("kind", [
  z.object({
    kind: z.literal("run_started"),
    run_id: z.string(),
    matter_id: z.string(),
    task: z.string(),
    rubric_version: z.string(),
    config_digest: z.string(),
    mode: runMode.default("autonomous"),
  }),
  z.object({ kind: z.literal("stage_started"), stage: z.string() }),
  z.object({ kind: z.literal("turn_completed"), record: turnRecord }),
  z.object({
    kind: z.literal("turn_discarded"),
    turn_id: z.string(),
    reason: z.string(),
    attempt: z.number().int(),
  }),
  z.object({ kind: z.literal("gate_evaluated"), turn_id: z.string(), result: gateResult }),
  z.object({
    kind: z.literal("spend_recorded"),
    turn_id: z.string(),
    input_tokens: z.number().int(),
    cache_read: z.number().int(),
    cache_write: z.number().int(),
    output_tokens: z.number().int(),
    model: z.string(),
    usd_equiv: z.number(),
    billing_mode: billingMode.default("subscription"),
  }),
  z.object({ kind: z.literal("run_finished"), status: runStatus }),
  z.object({ kind: z.literal("cap_raised"), to_usd: z.number() }),
  z.object({
    kind: z.literal("decision_recorded"),
    decision_id: z.string(),
    decision_kind: z.string(),
    action: z.string(),
    status: z.string(),
    decided_by: z.string(),
    source: z.string(),
    decided_at: z.string(),
  }),
  z.object({ kind: z.literal("checkpoint_reached"), boundary: z.string() }),
  z.object({ kind: z.literal("checkpoint_cleared"), boundary: z.string() }),
  z.object({ kind: z.literal("run_paused"), reason: z.string() }),
  z.object({ kind: z.literal("run_resumed") }),
  z.object({
    kind: z.literal("turn_intent"),
    turn_id: z.string(),
    model: z.string(),
    billing_mode: billingMode,
    max_plausible_usd: z.number(),
  }),
]);

export type RunEvent = z.infer<typeof runEventSchema>;
export type RunEventKind = RunEvent["kind"];
export type TurnRecord = z.infer<typeof turnRecord>;
export type SseGateResult = z.infer<typeof gateResult>;

/** A parsed frame, a recognized heartbeat, or an invalid payload (rejected). */
export type ParsedFrame =
  | { type: "event"; event: RunEvent }
  | { type: "heartbeat" }
  | { type: "invalid"; error: z.ZodError; raw: string };

/** Parse one SSE `data:` payload string at the trust boundary. */
export function parseRunEvent(raw: string): ParsedFrame {
  const trimmed = raw.trim();
  if (trimmed === "" || trimmed === "keep-alive" || trimmed.startsWith(":")) {
    return { type: "heartbeat" };
  }
  let json: unknown;
  try {
    json = JSON.parse(trimmed);
  } catch {
    return {
      type: "invalid",
      error: new z.ZodError([{ code: "custom", message: "not JSON", path: [], input: raw }]),
      raw,
    };
  }
  const result = runEventSchema.safeParse(json);
  if (result.success) return { type: "event", event: result.data };
  return { type: "invalid", error: result.error, raw };
}
