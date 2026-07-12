import type { RunStatus } from "./api/types";

/** Mono $ figure for the ledger voice (FD-9). */
export function usd(n: number | null | undefined): string {
  if (n == null) return "—";
  return `$${n.toFixed(2)}`;
}

/** Human label + tone for a run status (drives the status stamp color). */
export function statusMeta(status: RunStatus): { label: string; tone: "pass" | "pending" | "fail" | "ink" } {
  switch (status) {
    case "running":
      return { label: "Running", tone: "ink" };
    case "finished":
      return { label: "Finished", tone: "pass" };
    case "needs_attention":
      return { label: "Needs Attention", tone: "fail" };
    case "capped":
      return { label: "Capped", tone: "fail" };
    case "needs_decisions":
      return { label: "Needs Decisions", tone: "pending" };
    case "checkpoint":
      return { label: "Checkpoint", tone: "pending" };
    case "paused":
      return { label: "Paused", tone: "pending" };
    default: {
      const _never: never = status;
      return _never;
    }
  }
}

export const PERSONA_ORDER = [
  "associate",
  "partner",
  "oc_associate",
  "oc_partner",
  "judge",
  "juror",
] as const;

export function personaLabel(persona: string): string {
  return persona
    .split("_")
    .map((p) => (p === "oc" ? "OC" : p.charAt(0).toUpperCase() + p.slice(1)))
    .join(" ");
}
