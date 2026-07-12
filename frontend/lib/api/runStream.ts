/**
 * Pure SSE reducer (FD-8/FD-9): folds the run-event stream into cockpit timeline
 * state. Kept pure and free of React/EventSource so it can be tested against
 * synthetic events. Mirrors the backend fold's spend/status semantics closely enough
 * for the instrument band; the journal remains the authoritative source on reload.
 *
 * "One thing moves at a time" (FD-9): each event appends exactly one inked ledger line.
 */
import type { RunEvent, RunEventKind } from "./events";
import type { RunStatus } from "./types";

export interface TimelineLine {
  id: string;
  kind: RunEventKind;
  stage: string | null;
  persona: string | null;
  label: string;
  detail?: string;
  at?: string;
}

export interface RunStreamState {
  lines: TimelineLine[];
  currentStage: string | null;
  activePersona: string | null;
  status: RunStatus;
  spendUsd: number;
  pendingIntents: Record<string, number>;
  completedTurns: number;
  discardedTurns: number;
  pauseReason: string | null;
  capUsd: number | null;
  seq: number;
}

export function initialRunStreamState(status: RunStatus = "running"): RunStreamState {
  return {
    lines: [],
    currentStage: null,
    activePersona: null,
    status,
    spendUsd: 0,
    pendingIntents: {},
    completedTurns: 0,
    discardedTurns: 0,
    pauseReason: null,
    capUsd: null,
    seq: 0,
  };
}

/** Sum of unreconciled write-ahead intents — the conservative in-flight spend. */
export function pendingIntentTotal(state: RunStreamState): number {
  return Object.values(state.pendingIntents).reduce((a, b) => a + b, 0);
}

function usd(n: number): string {
  return `$${n.toFixed(2)}`;
}

/** Fold one validated event into the timeline state (pure). */
export function reduceRunEvent(state: RunStreamState, event: RunEvent): RunStreamState {
  const seq = state.seq + 1;
  const base = { ...state, seq };
  const line = (partial: Omit<TimelineLine, "id" | "kind" | "stage" | "persona">): TimelineLine => ({
    id: `${seq}`,
    kind: event.kind,
    stage: base.currentStage,
    persona: base.activePersona,
    ...partial,
  });

  switch (event.kind) {
    case "run_started":
      return {
        ...base,
        status: "running",
        lines: [...state.lines, line({ label: `Run opened — ${event.task}` })],
      };
    case "stage_started":
      return {
        ...base,
        currentStage: event.stage,
        lines: [...state.lines, { ...line({ label: `Stage — ${event.stage}` }), stage: event.stage }],
      };
    case "turn_completed": {
      const persona = event.record.spec.persona;
      return {
        ...base,
        activePersona: persona,
        completedTurns: state.completedTurns + 1,
        lines: [
          ...state.lines,
          {
            ...line({
              label: `${persona} completed a turn`,
              detail: event.record.spec.output_schema_name,
              at: event.record.completed_at,
            }),
            persona,
          },
        ],
      };
    }
    case "turn_discarded":
      return {
        ...base,
        discardedTurns: state.discardedTurns + 1,
        lines: [
          ...state.lines,
          line({ label: `Turn discarded — ${event.reason}`, detail: `attempt ${event.attempt}` }),
        ],
      };
    case "gate_evaluated":
      return {
        ...base,
        lines: [
          ...state.lines,
          line({ label: `Gate ${event.result.gate} — ${event.result.status}` }),
        ],
      };
    case "turn_intent":
      return {
        ...base,
        pendingIntents: { ...state.pendingIntents, [event.turn_id]: event.max_plausible_usd },
        lines: [
          ...state.lines,
          line({ label: `Turn reserved`, detail: `≤ ${usd(event.max_plausible_usd)}` }),
        ],
      };
    case "spend_recorded": {
      const { [event.turn_id]: _reconciled, ...rest } = state.pendingIntents;
      return {
        ...base,
        spendUsd: state.spendUsd + event.usd_equiv,
        pendingIntents: rest,
        lines: [
          ...state.lines,
          line({ label: `Metered ${usd(event.usd_equiv)}`, detail: event.model }),
        ],
      };
    }
    case "cap_raised":
      return {
        ...base,
        capUsd: event.to_usd,
        status: state.status === "capped" ? "running" : state.status,
        lines: [...state.lines, line({ label: `Cap raised to ${usd(event.to_usd)}` })],
      };
    case "decision_recorded":
      return {
        ...base,
        lines: [
          ...state.lines,
          line({ label: `Decision ${event.action} — ${event.decision_kind}`, at: event.decided_at }),
        ],
      };
    case "checkpoint_reached":
      return {
        ...base,
        status: "checkpoint",
        lines: [...state.lines, line({ label: `Checkpoint — ${event.boundary}` })],
      };
    case "checkpoint_cleared":
      return {
        ...base,
        status: "running",
        lines: [...state.lines, line({ label: `Checkpoint cleared — ${event.boundary}` })],
      };
    case "run_paused":
      return {
        ...base,
        status: "paused",
        pauseReason: event.reason,
        lines: [...state.lines, line({ label: `Paused — ${event.reason}` })],
      };
    case "run_resumed":
      return {
        ...base,
        status: "running",
        pauseReason: null,
        lines: [...state.lines, line({ label: `Resumed` })],
      };
    case "run_finished":
      return {
        ...base,
        status: event.status,
        activePersona: null,
        lines: [...state.lines, line({ label: `Run ${event.status}` })],
      };
    default: {
      // Exhaustiveness: every RunEventKind is handled above.
      const _never: never = event;
      return _never;
    }
  }
}

/** Fold a whole batch (used by tests and initial cache hydration). */
export function reduceRunEvents(
  events: RunEvent[],
  initial: RunStreamState = initialRunStreamState(),
): RunStreamState {
  return events.reduce(reduceRunEvent, initial);
}
