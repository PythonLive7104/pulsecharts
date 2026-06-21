// Dark/light toggle button. Works on every page (landing + auth + dashboard).
import { useTheme } from "../theme";

export default function ThemeToggle() {
  const theme = useTheme((s) => s.theme);
  const toggle = useTheme((s) => s.toggle);
  return (
    <button
      className="theme-toggle"
      onClick={toggle}
      title={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
      aria-label="Toggle theme"
    >
      {theme === "dark" ? "☀️" : "🌙"}
    </button>
  );
}
