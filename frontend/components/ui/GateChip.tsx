import { cn } from "@/lib/utils/cn";

type GateStatus = "pass" | "fail" | "pending";

const STYLES: Record<GateStatus, string> = {
  pass: "text-pass bg-pass-bg border-pass/45",
  fail: "text-fail bg-fail-bg border-fail/45",
  pending: "text-pending bg-pending-bg border-pending/45",
};

/** A per-gate chip in the record voice (mono), colored by verdict. */
export function GateChip({ gate, status }: { gate: string; status: GateStatus }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap rounded-[3px] border px-[0.45rem] py-[0.14rem] font-mono text-[0.6rem] tracking-[0.05em]",
        STYLES[status],
      )}
    >
      <span>{gate}</span>
      <span aria-hidden="true">·</span>
      <span>{status}</span>
    </span>
  );
}
