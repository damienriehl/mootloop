"use client";

import { useEffect, useState } from "react";

type Theme = "light" | "dark";
const STORAGE_KEY = "mootloop-theme";

/** Read the theme the pre-paint script already applied to <html>. */
function currentTheme(): Theme {
  if (typeof document === "undefined") return "light";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
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
  const [theme, setTheme] = useState<Theme>("light");
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setTheme(currentTheme());
    setMounted(true);
  }, []);

  function toggle() {
    const next: Theme = theme === "dark" ? "light" : "dark";
    setTheme(next);
    applyTheme(next);
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
      className="flex h-8 w-8 items-center justify-center rounded-full border border-rule-strong text-ink-soft transition-colors hover:border-accent hover:text-accent"
    >
      <span aria-hidden="true">{mounted && theme === "dark" ? "☾" : "☀"}</span>
    </button>
  );
}
