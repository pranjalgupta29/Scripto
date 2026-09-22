"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "scripto-theme";

export type Theme = "light" | "dark";

function systemTheme(): Theme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

export function useTheme() {
  // Always starts at "light" so the first client render matches the server
  // render exactly (the server has no theme signal). The inline anti-flash
  // script in layout.tsx already set the real class on <html> before paint,
  // so the page never visibly flashes light -- this effect just brings React
  // state in sync a moment after hydration, which is when the icon can
  // safely change without a hydration mismatch.
  const [theme, setThemeState] = useState<Theme>("light");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY) as Theme | null;
    setThemeState(stored ?? systemTheme());
  }, []);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    applyTheme(next);
    try {
      window.localStorage.setItem(STORAGE_KEY, next);
    } catch {
      // Private browsing / storage disabled -- theme just won't persist.
    }
  }, []);

  const toggle = useCallback(() => {
    setTheme(theme === "dark" ? "light" : "dark");
  }, [theme, setTheme]);

  return { theme, setTheme, toggle };
}
