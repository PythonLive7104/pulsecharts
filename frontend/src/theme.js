// Dark/light theme (persisted). Applies `data-theme` to <html>; styles.css
// defines the CSS-variable palette per theme. Light is the default; the toggle
// still lets users switch to dark, and their choice is remembered.
import { create } from "zustand";

// Bumped to .v2 when light became the default — drops any stale saved "dark" so
// existing visitors get the new default (they can re-toggle to dark if they want).
const KEY = "pulsecharts.theme.v2";

function initial() {
  const saved = localStorage.getItem(KEY);
  if (saved === "light" || saved === "dark") return saved;
  return "light";  // default to light for first-time visitors
}

function apply(theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

export const useTheme = create((set, get) => ({
  theme: initial(),
  init() {
    apply(get().theme);
  },
  toggle() {
    const next = get().theme === "dark" ? "light" : "dark";
    localStorage.setItem(KEY, next);
    apply(next);
    set({ theme: next });
  },
}));
