import { cn } from "@/lib/utils/cn";
import { statusMeta } from "@/lib/format";
import type { RunStatus } from "@/lib/api/types";

const TONE: Record<string, string> = {
  pass: "text-pass border-pass",
  pending: "text-pending border-pending",
  fail: "text-fail border-fail",
  ink: "text-accent border-accent",
};

/** The run status rendered as an inked case stamp (FD-9). */
export function StatusStamp({ status, className }: { status: RunStatus; className?: string }) {
  const { label, tone } = statusMeta(status);
  return (
    <span
      className={cn(
        "inline-block -rotate-2 rounded-sm border-2 px-3 py-1 font-mono text-[0.7rem] font-bold uppercase tracking-[0.14em]",
        TONE[tone],
        className,
      )}
    >
      {label}
    </span>
  );
}
