import { cn } from "@/lib/utils/cn";
import { PERSONA_ORDER, personaLabel } from "@/lib/format";

/** The six-persona pipeline strip; the active persona is lit (FD-9). */
export function PersonaStrip({
  activePersona,
  turnsByPersona,
}: {
  activePersona: string | null;
  turnsByPersona: Record<string, number>;
}) {
  return (
    <ol
      className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6"
      aria-label="Persona pipeline"
    >
      {PERSONA_ORDER.map((persona) => {
        const active = persona === activePersona;
        const count = turnsByPersona[persona] ?? 0;
        return (
          <li
            key={persona}
            aria-current={active ? "step" : undefined}
            className={cn(
              "border border-rule border-l-[3px] bg-paper-raised px-3 py-2 transition-colors",
              active ? "border-l-accent bg-accent-soft" : "border-l-rule-strong",
            )}
          >
            <span
              className={cn(
                "block font-mono text-[0.62rem] uppercase tracking-[0.1em]",
                active ? "text-accent" : "text-ink-soft",
              )}
            >
              {personaLabel(persona)}
            </span>
            <span className="mt-0.5 block font-mono text-sm font-bold">
              {count}
              <span className="ml-1 text-[0.65rem] font-normal text-ink-faint">turns</span>
            </span>
          </li>
        );
      })}
    </ol>
  );
}
