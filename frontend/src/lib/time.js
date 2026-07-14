// Human-readable timestamps.
//
// A signal card's timestamp answers one question: "how fresh is this setup?" A raw
// "7/14/2026, 5:12:23 PM" makes the reader do date arithmetic to find out — and the
// seconds are noise on a 1h/4h signal. Relative time answers it directly; the exact
// timestamp stays available on hover for anyone reconciling against a chart.

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** "just now" | "12m ago" | "3h ago" | "2d ago" | a date once it's older than a week. */
export function timeAgo(iso, now = Date.now()) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = now - then;

  if (diff < 0) return "just now";           // clock skew — never say "in 3 minutes"
  if (diff < MINUTE) return "just now";
  if (diff < HOUR) return `${Math.floor(diff / MINUTE)}m ago`;
  if (diff < DAY) return `${Math.floor(diff / HOUR)}h ago`;
  if (diff < 7 * DAY) return `${Math.floor(diff / DAY)}d ago`;
  // Beyond a week "38d ago" stops being useful — show the date.
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/** Full timestamp for tooltips: "Tue 14 Jul, 17:12". Seconds dropped — they never matter. */
export function fullTime(iso) {
  if (!iso) return "";
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short", day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit",
  });
}
