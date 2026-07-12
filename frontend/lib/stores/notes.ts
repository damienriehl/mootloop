"use client";

/**
 * Draft-note persistence for decision resolution (FD-8: Zustand persist for DRAFTS
 * ONLY — never server truth). Notes an attorney types survive a reload/nav so an
 * in-progress ruling is never lost; they clear once the decision is entered.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface NotesState {
  drafts: Record<string, string>;
  setDraft: (decisionId: string, note: string) => void;
  clearDraft: (decisionId: string) => void;
}

export const useNotesStore = create<NotesState>()(
  persist(
    (set) => ({
      drafts: {},
      setDraft: (decisionId, note) =>
        set((s) => ({ drafts: { ...s.drafts, [decisionId]: note } })),
      clearDraft: (decisionId) =>
        set((s) => {
          const { [decisionId]: _removed, ...rest } = s.drafts;
          return { drafts: rest };
        }),
    }),
    {
      name: "mootloop-decision-notes",
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
