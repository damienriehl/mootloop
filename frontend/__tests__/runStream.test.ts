import { describe, expect, it } from "vitest";
import {
  initialRunStreamState,
  pendingIntentTotal,
  reduceRunEvent,
  reduceRunEvents,
} from "@/lib/api/runStream";
import type { RunEvent } from "@/lib/api/events";

function turnCompleted(persona: string, turnId: string): RunEvent {
  return {
    kind: "turn_completed",
    record: {
      spec: {
        turn_id: turnId,
        run_id: "r1",
        persona,
        stage: "draft",
        output_schema_name: "draft",
        attempt: 1,
        prompt_context: {},
      },
      output: {},
      gate_results: [],
      completed_at: "2026-07-12T00:00:00Z",
    },
  };
}

describe("SSE reducer (synthetic events)", () => {
  it("folds a full lifecycle into correct timeline state", () => {
    const events: RunEvent[] = [
      { kind: "run_started", run_id: "r1", matter_id: "m1", task: "discovery", rubric_version: "v1", config_digest: "abc", mode: "autonomous" },
      { kind: "stage_started", stage: "draft" },
      { kind: "turn_intent", turn_id: "t1", model: "sonnet", billing_mode: "subscription", max_plausible_usd: 0.5 },
      turnCompleted("associate", "t1"),
      { kind: "spend_recorded", turn_id: "t1", input_tokens: 10, cache_read: 0, cache_write: 0, output_tokens: 5, model: "sonnet", usd_equiv: 0.2, billing_mode: "subscription" },
      { kind: "gate_evaluated", turn_id: "t1", result: { status: "pass", gate: "citations", findings: [] } },
      turnCompleted("partner", "t2"),
    ];

    const state = reduceRunEvents(events);

    expect(state.status).toBe("running");
    expect(state.currentStage).toBe("draft");
    expect(state.completedTurns).toBe(2);
    expect(state.activePersona).toBe("partner");
    expect(state.spendUsd).toBeCloseTo(0.2);
    // The intent for t1 was reconciled by its spend_recorded — no pending left.
    expect(pendingIntentTotal(state)).toBe(0);
    // One ledger line inked per event ("one thing moves at a time").
    expect(state.lines).toHaveLength(events.length);
  });

  it("holds unreconciled intents as conservative pending spend", () => {
    let state = initialRunStreamState();
    state = reduceRunEvent(state, { kind: "turn_intent", turn_id: "t9", model: "opus", billing_mode: "api", max_plausible_usd: 1.25 });
    expect(state.spendUsd).toBe(0);
    expect(pendingIntentTotal(state)).toBeCloseTo(1.25);
  });

  it("tracks pause/resume and cap transitions", () => {
    let state = initialRunStreamState();
    state = reduceRunEvent(state, { kind: "run_paused", reason: "capacity" });
    expect(state.status).toBe("paused");
    expect(state.pauseReason).toBe("capacity");

    state = reduceRunEvent(state, { kind: "run_resumed" });
    expect(state.status).toBe("running");
    expect(state.pauseReason).toBeNull();

    state = { ...state, status: "capped" };
    state = reduceRunEvent(state, { kind: "cap_raised", to_usd: 30 });
    expect(state.status).toBe("running");
    expect(state.capUsd).toBe(30);
  });

  it("marks the terminal status on run_finished and clears the active persona", () => {
    let state = reduceRunEvents([turnCompleted("judge", "t1")]);
    state = reduceRunEvent(state, { kind: "run_finished", status: "finished" });
    expect(state.status).toBe("finished");
    expect(state.activePersona).toBeNull();
  });
});
