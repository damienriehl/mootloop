"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { listMatters } from "@/lib/api/matters";
import { keys } from "@/lib/api/keys";
import { ThemeToggle } from "@/components/ThemeToggle";

export default function HomePage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: keys.matters(),
    queryFn: () => listMatters(),
  });

  return (
    <div className="mx-auto max-w-4xl px-5 py-10">
      <header className="mb-8 flex items-center justify-between border-b-[3px] border-double border-rule-strong pb-4">
        <div className="flex items-center gap-3">
          <span className="text-2xl leading-none text-accent" aria-hidden="true">
            §
          </span>
          <div>
            <h1 className="text-xl font-bold tracking-tight">MootLoop</h1>
            <p className="font-mono text-[0.68rem] uppercase tracking-[0.08em] text-ink-faint">
              Matter Cockpit
            </p>
          </div>
        </div>
        <ThemeToggle />
      </header>

      <main id="main">
        <h2 className="mb-1 font-mono text-[0.7rem] uppercase tracking-[0.12em] text-ink-faint">
          Docket
        </h2>
        <p className="mb-6 max-w-prose text-ink-soft">
          Select a matter to open its runs or clear its decision inbox.
        </p>

        {isLoading && <p className="font-mono text-sm text-ink-faint">Loading matters…</p>}
        {isError && (
          <p role="alert" className="font-mono text-sm text-fail">
            Could not load matters: {(error as Error).message}
          </p>
        )}

        <ul className="grid gap-3">
          {data?.map((matter) => (
            <li key={matter.matter_id}>
              <Link
                href={`/matters/${matter.matter_id}/runs`}
                className="block border border-rule border-l-4 border-l-accent bg-paper-raised px-5 py-4 shadow-ledger transition-colors hover:border-l-accent hover:bg-accent-soft"
              >
                <div className="flex items-baseline justify-between gap-3">
                  <span className="font-bold">{matter.display_name}</span>
                  <span className="font-mono text-xs text-ink-faint">{matter.case_number}</span>
                </div>
                <span className="mt-1 block font-mono text-[0.7rem] uppercase tracking-[0.08em] text-ink-faint">
                  {matter.loaded ? "loaded" : "registered"} · {matter.matter_id}
                </span>
              </Link>
            </li>
          ))}
          {data && data.length === 0 && (
            <li className="font-mono text-sm text-ink-faint">No matters registered.</li>
          )}
        </ul>
      </main>
    </div>
  );
}
