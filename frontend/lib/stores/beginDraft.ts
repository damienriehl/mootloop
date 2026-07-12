"use client";

/**
 * In-progress freeform-intent persistence for the begin-task omnibox (FD-8: Zustand
 * persist for DRAFTS ONLY — never server truth). What an attorney is typing survives a
 * reload / nav so a half-written task intent is never lost; it clears once a run starts
 * from the resolved slip. Keyed per matter so drafts don't bleed across matters.
 */
import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

interface BeginDraftState {
  intents: Record<string, string>;
  setIntent: (matterId: string, text: string) => void;
  clearIntent: (matterId: string) => void;
}

export const useBeginDraftStore = create<BeginDraftState>()(
  persist(
    (set) => ({
      intents: {},
      setIntent: (matterId, text) =>
        set((s) => ({ intents: { ...s.intents, [matterId]: text } })),
      clearIntent: (matterId) =>
        set((s) => {
          const { [matterId]: _removed, ...rest } = s.intents;
          return { intents: rest };
        }),
    }),
    {
      name: "mootloop-begin-intents",
      storage: createJSONStorage(() => localStorage),
    },
  ),
);
