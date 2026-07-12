import type { TimelineLine } from "@/lib/api/runStream";
import { personaLabel } from "@/lib/format";

/** The SSE iteration timeline — each event inks in as one ledger line (FD-9).
 *  aria-live=polite so a screen reader announces new lines without interrupting. */
export function Timeline({ lines }: { lines: TimelineLine[] }) {
  return (
    <ol
      aria-live="polite"
      aria-label="Run timeline"
      className="relative m-0 list-none border-l-2 border-rule-strong pl-0"
    >
      {lines.length === 0 && (
        <li className="py-2 pl-6 font-mono text-sm text-ink-faint">
          Awaiting the first ledger entry…
        </li>
      )}
      {lines.map((line, idx) => {
        const newest = idx === lines.length - 1;
        return (
          <li
            key={line.id}
            className={`relative py-1.5 pl-6 ${newest ? "ink-in" : ""}`}
          >
            <span
              aria-hidden="true"
              className="absolute -left-[0.36rem] top-3 h-2.5 w-2.5 rounded-full border-2 border-accent bg-paper-raised"
            />
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
              {line.stage && (
                <span className="font-mono text-[0.62rem] uppercase tracking-[0.08em] text-accent">
                  {line.stage}
                </span>
              )}
              <span className="text-sm">{line.label}</span>
              {line.persona && (
                <span className="font-mono text-[0.62rem] text-ink-faint">
                  {personaLabel(line.persona)}
                </span>
              )}
              {line.detail && (
                <span className="font-mono text-[0.62rem] text-ink-faint">{line.detail}</span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
