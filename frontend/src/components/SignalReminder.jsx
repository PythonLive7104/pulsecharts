// Reminder shown every time the chart workspace opens: the number of signals a
// user receives scales with how many symbols they watch and how many strategies
// they follow (signals are generated per watched-symbol × followed-strategy).
// Nudges users to broaden both.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

function count(res) {
  if (Array.isArray(res)) return res.length;
  if (Array.isArray(res?.results)) return res.results.length;
  return 0;
}

export default function SignalReminder() {
  const [open, setOpen] = useState(false);
  const [stats, setStats] = useState({ symbols: 0, strategies: 0 });

  useEffect(() => {
    let alive = true;
    Promise.allSettled([api.watchlist(), api.signalSubscriptions()]).then(
      ([wl, subs]) => {
        if (!alive) return;
        setStats({
          symbols: wl.status === "fulfilled" ? count(wl.value) : 0,
          strategies: subs.status === "fulfilled" ? count(subs.value) : 0,
        });
        setOpen(true);
      }
    );
    return () => { alive = false; };
  }, []);

  if (!open) return null;

  const close = () => setOpen(false);

  return (
    <div className="reminder-backdrop" role="dialog" aria-modal="true" aria-labelledby="reminder-title" onClick={close}>
      <div className="reminder-card" onClick={(e) => e.stopPropagation()}>
        <button className="reminder-x" aria-label="Close" onClick={close}>✕</button>
        <div className="reminder-icon">📡</div>
        <h2 id="reminder-title">Want more signals?</h2>
        <p className="reminder-text">
          Signals are generated for <strong>each symbol you add to your watchlist on this page</strong> across{" "}
          <strong>each strategy you follow on the signal page</strong>. The more you add to your
          watchlist and the more strategies you follow, the more signals land in
          your feed and Telegram.
        </p>

        <div className="reminder-stats">
          <div className="reminder-stat">
            <span className="reminder-stat-num">{stats.symbols}</span>
            <span className="reminder-stat-label">symbols watched</span>
          </div>
          <span className="reminder-times">×</span>
          <div className="reminder-stat">
            <span className="reminder-stat-num">{stats.strategies}</span>
            <span className="reminder-stat-label">strategies followed</span>
          </div>
        </div>

        <p className="reminder-tip">
          💡 Tip: add as many symbols as your plan allows from the Watchlist panel,
          then follow several strategies to widen your coverage.
        </p>

        <div className="reminder-actions">
          <Link to="/signals" className="btn-primary" onClick={close}>Follow strategies →</Link>
          <button className="btn-ghost" onClick={close}>Got it</button>
        </div>
      </div>
    </div>
  );
}
