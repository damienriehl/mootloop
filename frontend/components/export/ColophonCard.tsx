import { cn } from "@/lib/utils/cn";

/**
 * The certify-and-release COLOPHON (FD-9): the record-voice disclosures that accompany
 * every deliverable release — run id, rubric version, attestation state, citator /
 * citation disclosure. Fields the FE-2.5 read endpoints do NOT yet expose are shown
 * HONESTLY as "—" / pending rather than fabricated (rubric version and citator
 * disclosure arrive with the run-detail extensions in a later phase).
 */
export interface ColophonFields {
  runId: string;
  rubricVersion: string | null;
  attestationState: string;
  attestationTone: "pass" | "pending" | "fail";
  citatorDisclosure: string | null;
}

export function ColophonCard({ fields }: { fields: ColophonFields }) {
  const rows: { label: string; value: string; pending?: boolean; tone?: string }[] = [
    { label: "run", value: fields.runId },
    {
      label: "rubric",
      value: fields.rubricVersion ?? "— (not exposed by v1 read)",
      pending: fields.rubricVersion == null,
    },
    {
      label: "attestation",
      value: fields.attestationState,
      tone:
        fields.attestationTone === "pass"
          ? "text-pass"
          : fields.attestationTone === "fail"
            ? "text-fail"
            : "text-pending",
    },
    {
      label: "citator",
      value: fields.citatorDisclosure ?? "— (not exposed by v1 read)",
      pending: fields.citatorDisclosure == null,
    },
  ];

  return (
    <div data-testid="colophon" className="border border-double border-rule-strong bg-paper p-4">
      <p className="mb-3 font-mono text-[0.62rem] uppercase tracking-[0.14em] text-ink-faint">
        Colophon · certify &amp; release
      </p>
      <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-1.5 font-mono text-xs">
        {rows.map((r) => (
          <div key={r.label} className="contents">
            <dt className="text-ink-faint">{r.label}</dt>
            <dd
              className={cn(
                "break-all",
                r.pending ? "text-ink-faint italic" : (r.tone ?? "text-ink"),
              )}
            >
              {r.value}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
