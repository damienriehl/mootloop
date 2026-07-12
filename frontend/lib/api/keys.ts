/**
 * Hierarchical TanStack Query key factories (FD-8).
 *
 * Keys nest so a broad `invalidateQueries` at any level sweeps everything beneath it:
 *
 *   keys.matter(id).key                       → ["mootloop","matters",id]
 *   keys.matter(id).runs()                    → [...,"runs"]
 *   keys.matter(id).run(runId).key            → [...,"runs",runId]
 *   keys.matter(id).run(runId).gates()        → [...,"runs",runId,"gates"]
 *   keys.matter(id).run(runId).decisions()    → [...,"runs",runId,"decisions"]
 *
 * The SSE reducer and mutations invalidate/patch through the exact same keys, so the
 * Query cache stays the single source of server truth (no useState mirrors).
 */

const ROOT = "mootloop" as const;

export const keys = {
  all: [ROOT] as const,
  matters: () => [ROOT, "matters"] as const,
  matter: (matterId: string) => {
    const key = [ROOT, "matters", matterId] as const;
    return {
      key,
      runs: () => [...key, "runs"] as const,
      run: (runId: string) => {
        const runKey = [...key, "runs", runId] as const;
        return {
          key: runKey,
          detail: () => [...runKey, "detail"] as const,
          gates: () => [...runKey, "gates"] as const,
          decisions: () => [...runKey, "decisions"] as const,
          requests: () => [...runKey, "requests"] as const,
        };
      },
    };
  },
} as const;

export type MatterKeyFactory = ReturnType<typeof keys.matter>;
export type RunKeyFactory = ReturnType<MatterKeyFactory["run"]>;
