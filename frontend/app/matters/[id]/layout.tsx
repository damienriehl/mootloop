"use client";

import Link from "next/link";
import { usePathname, useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { listMatters } from "@/lib/api/matters";
import { keys } from "@/lib/api/keys";
import { cn } from "@/lib/utils/cn";
import { ThemeToggle } from "@/components/ThemeToggle";

interface DocketTab {
  href: string;
  label: string;
  match: (path: string) => boolean;
}

/** The pleading spine: matter caption at the head, room tabs as docket entries (FD-9). */
export default function MatterLayout({ children }: { children: ReactNode }) {
  const params = useParams<{ id: string }>();
  const matterId = params.id;
  const pathname = usePathname();

  const { data: matters } = useQuery({
    queryKey: keys.matters(),
    queryFn: () => listMatters(),
  });
  const matter = matters?.find((m) => m.matter_id === matterId);

  const base = `/matters/${matterId}`;
  const tabs: DocketTab[] = [
    { href: `${base}/runs`, label: "Runs", match: (p) => p.includes("/runs") },
    { href: `${base}/inbox`, label: "Decision Inbox", match: (p) => p.endsWith("/inbox") },
  ];

  return (
    <div className="mx-auto flex min-h-screen max-w-6xl flex-col md:flex-row">
      {/* Left rail (desktop) — the pleading spine */}
      <aside className="hidden shrink-0 border-r border-rule bg-paper-raised/60 md:block md:w-64">
        <div className="sticky top-0 flex h-screen flex-col">
          <div className="border-b-[3px] border-double border-rule-strong px-5 py-4">
            <Link href="/" className="flex items-center gap-2 no-underline">
              <span className="text-xl text-accent" aria-hidden="true">
                §
              </span>
              <span className="font-bold tracking-tight text-ink">MootLoop</span>
            </Link>
          </div>

          {/* Matter caption */}
          <div className="border-b border-rule px-5 py-4">
            <p className="font-mono text-[0.62rem] uppercase tracking-[0.14em] text-ink-faint">
              In re
            </p>
            <p className="mt-1 font-bold leading-snug">{matter?.display_name ?? matterId}</p>
            {matter?.case_number && (
              <p className="mt-1 font-mono text-xs text-ink-soft">{matter.case_number}</p>
            )}
          </div>

          {/* Room tabs (docket) */}
          <nav aria-label="Matter rooms" className="flex flex-col gap-1 px-3 py-4">
            {tabs.map((tab) => {
              const active = tab.match(pathname);
              return (
                <Link
                  key={tab.href}
                  href={tab.href}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "border-l-[3px] px-3 py-2 no-underline transition-colors",
                    active
                      ? "border-l-accent bg-accent-soft font-bold text-ink"
                      : "border-l-transparent text-ink-soft hover:border-l-rule-strong hover:text-accent",
                  )}
                >
                  {tab.label}
                </Link>
              );
            })}
          </nav>

          <div className="mt-auto flex items-center justify-between border-t border-rule px-5 py-4">
            <span className="font-mono text-[0.62rem] uppercase tracking-[0.1em] text-ink-faint">
              Theme
            </span>
            <ThemeToggle />
          </div>
        </div>
      </aside>

      {/* Content */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile masthead */}
        <header className="flex items-center justify-between border-b border-rule bg-paper-raised/60 px-4 py-3 md:hidden">
          <Link href="/" className="flex items-center gap-2 no-underline">
            <span className="text-lg text-accent" aria-hidden="true">
              §
            </span>
            <span className="truncate font-bold text-ink">{matter?.display_name ?? matterId}</span>
          </Link>
          <ThemeToggle />
        </header>

        <main id="main" className="flex-1 px-4 pb-24 pt-5 md:px-8 md:pb-10">
          {children}
        </main>

        {/* Bottom tab bar (mobile) */}
        <nav
          aria-label="Matter rooms"
          className="fixed inset-x-0 bottom-0 z-10 flex border-t border-rule bg-paper-raised md:hidden"
        >
          {tabs.map((tab) => {
            const active = tab.match(pathname);
            return (
              <Link
                key={tab.href}
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex-1 border-t-2 py-3 text-center text-sm no-underline",
                  active
                    ? "border-t-accent font-bold text-accent"
                    : "border-t-transparent text-ink-soft",
                )}
              >
                {tab.label}
              </Link>
            );
          })}
        </nav>
      </div>
    </div>
  );
}
