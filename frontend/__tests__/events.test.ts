import { describe, expect, it } from "vitest";
import { parseRunEvent, runEventSchema } from "@/lib/api/events";

describe("zod SSE event matrix", () => {
  it("parses a valid run_started frame", () => {
    const raw = JSON.stringify({
      kind: "run_started",
      run_id: "r1",
      matter_id: "m1",
      task: "discovery",
      rubric_version: "v1",
      config_digest: "abc",
      mode: "gated",
    });
    const parsed = parseRunEvent(raw);
    expect(parsed.type).toBe("event");
    if (parsed.type === "event") {
      expect(parsed.event.kind).toBe("run_started");
    }
  });

  it("parses a nested turn_completed record", () => {
    const raw = JSON.stringify({
      kind: "turn_completed",
      record: {
        spec: {
          turn_id: "t1",
          run_id: "r1",
          persona: "associate",
          stage: "draft",
          output_schema_name: "draft",
          attempt: 1,
          prompt_context: {},
        },
        output: { text: "…" },
        gate_results: [{ status: "pass", gate: "citations", findings: [] }],
        completed_at: "2026-07-12T00:00:00Z",
      },
    });
    expect(parseRunEvent(raw).type).toBe("event");
  });

  it("treats blank and keep-alive payloads as heartbeats", () => {
    expect(parseRunEvent("").type).toBe("heartbeat");
    expect(parseRunEvent("  ").type).toBe("heartbeat");
    expect(parseRunEvent("keep-alive").type).toBe("heartbeat");
    expect(parseRunEvent(": keep-alive").type).toBe("heartbeat");
  });

  it("rejects an unknown discriminator", () => {
    const parsed = parseRunEvent(JSON.stringify({ kind: "not_a_real_event" }));
    expect(parsed.type).toBe("invalid");
  });

  it("rejects a valid-kind frame with a missing required field", () => {
    // run_finished requires `status`.
    const parsed = parseRunEvent(JSON.stringify({ kind: "run_finished" }));
    expect(parsed.type).toBe("invalid");
  });

  it("rejects a wrong-typed field", () => {
    const parsed = parseRunEvent(
      JSON.stringify({ kind: "cap_raised", to_usd: "thirty-dollars" }),
    );
    expect(parsed.type).toBe("invalid");
  });

  it("rejects non-JSON payloads without throwing", () => {
    expect(parseRunEvent("{not json").type).toBe("invalid");
  });

  it("accepts every event kind in the union via safeParse", () => {
    const samples = [
      { kind: "stage_started", stage: "draft" },
      { kind: "turn_discarded", turn_id: "t1", reason: "derailed", attempt: 2 },
      { kind: "gate_evaluated", turn_id: "t1", result: { status: "fail", gate: "g", findings: [] } },
      { kind: "spend_recorded", turn_id: "t1", input_tokens: 1, cache_read: 0, cache_write: 0, output_tokens: 1, model: "m", usd_equiv: 0.1, billing_mode: "api" },
      { kind: "decision_recorded", decision_id: "d1", decision_kind: "rfa_disposition", action: "approve", status: "approved", decided_by: "a@b.com", source: "human", decided_at: "t" },
      { kind: "checkpoint_reached", boundary: "stage:draft" },
      { kind: "checkpoint_cleared", boundary: "stage:draft" },
      { kind: "run_paused", reason: "capacity" },
      { kind: "run_resumed" },
      { kind: "turn_intent", turn_id: "t1", model: "m", billing_mode: "subscription", max_plausible_usd: 0.5 },
    ];
    for (const s of samples) {
      expect(runEventSchema.safeParse(s).success).toBe(true);
    }
  });
});
