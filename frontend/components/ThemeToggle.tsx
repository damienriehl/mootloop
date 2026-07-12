"use client";

import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";
const STORAGE_KEY = "mootloop-theme";

// A tiny external store over the <html> theme class. `useSyncExternalStore` reads the
// DOM the pre-paint script already themed (getSnapshot) with a stable server snapshot
// ("light"), so there is no hydration mismatch and no setState-in-effect (the toggle
// mutates the DOM and notifies subscribers, which re-renders the icon/aria).
const listeners = new Set<() => void>();

/** Read the theme the pre-paint script already applied to <html>. */
function currentTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function subscribe(onChange: () => void): () => void {
  listeners.add(onChange);
  if (typeof window !== "undefined") window.addEventListener("storage", onChange);
  return () => {
    listeners.delete(onChange);
    if (typeof window !== "undefined") window.removeEventListener("storage", onChange);
  };
}

function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  root.classList.remove("light", "dark");
  root.classList.add(theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    /* storage may be unavailable; the class still applies for this session. */
  }
  listeners.forEach((l) => l()); // notify this tab (the `storage` event only fires cross-tab)
}

/** Inline script (runs before paint) that applies the stored/OS theme — no FOUC. */
export const themeInitScript = `(() => {
  try {
    var t = localStorage.getItem("${STORAGE_KEY}");
    if (!t) t = matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    document.documentElement.classList.add(t);
  } catch (e) {
    document.documentElement.classList.add("light");
  }
})();`;

export function ThemeToggle() {
  // Server snapshot is always "light" (matches the SSR HTML); the client snapshot reads
  // whatever the pre-paint script applied. useSyncExternalStore reconciles the two
  // without a hydration warning.
  const theme = useSyncExternalStore(subscribe, currentTheme, () => "light" as Theme);

  function toggle() {
    applyTheme(theme === "dark" ? "light" : "dark");
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      className="flex h-8 w-8 items-center justify-center rounded-full border border-rule-strong text-ink-soft transition-colors hover:border-accent hover:text-accent"
    >
      <span aria-hidden="true">{theme === "dark" ? "☾" : "☀"}</span>
    </button>
  );
}
