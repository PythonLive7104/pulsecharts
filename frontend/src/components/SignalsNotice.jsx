// One-off announcement shown when the signals page opens: the engine's realized
// accuracy was deliberately reset to a worst-case baseline, and it is now running
// live again. Closing it reveals the page underneath, and it stays dismissed.
import { useState } from "react";

// Bump the suffix to re-show the notice to everyone after a future reset.
const SEEN_KEY = "signals_notice_seen_v1";

function alreadySeen() {
  try {
    return localStorage.getItem(SEEN_KEY) === "1";
  } catch {
    return false; // private mode / storage disabled — just show it
  }
}

export default function SignalsNotice() {
  const [open, setOpen] = useState(() => !alreadySeen());
  if (!open) return null;

  const close = () => {
    try {
      localStorage.setItem(SEEN_KEY, "1");
    } catch {
      /* storage disabled — dismissal just won't persist */
    }
    setOpen(false);
  };

  return (
    <div
      className="reminder-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="signals-notice-title"
      onClick={close}
    >
      <div className="reminder-card" onClick={(e) => e.stopPropagation()}>
        <button className="reminder-x" aria-label="Close" onClick={close}>✕</button>
        <div className="reminder-icon">🧪</div>
        <h2 id="signals-notice-title">Signals AI reset to a worst-case baseline</h2>
        <p className="reminder-text">
          We deliberately placed our signals AI in a worst-case scenario, which
          brought its realized accuracy down from <strong>85%</strong> to{" "}
          <strong>40%</strong>. It is now analysing the market again and
          generating fresh signals from that baseline.
        </p>
        <p className="reminder-text">
          The point of the exercise is to see whether it can climb back to 85%
          on its own. Expect the accuracy figure on this page to move while the
          test runs.
        </p>

        <div className="reminder-stats">
          <div className="reminder-stat">
            <span className="reminder-stat-num">85%</span>
            <span className="reminder-stat-label">before reset</span>
          </div>
          <span className="reminder-times">→</span>
          <div className="reminder-stat">
            <span className="reminder-stat-num">40%</span>
            <span className="reminder-stat-label">baseline now</span>
          </div>
        </div>

        <p className="reminder-tip">
          💡 Signals are algorithmic, informational output — not financial
          advice. Trade at your own risk.
        </p>

        <div className="reminder-actions">
          <button className="btn-primary" onClick={close}>Close</button>
        </div>
      </div>
    </div>
  );
}
