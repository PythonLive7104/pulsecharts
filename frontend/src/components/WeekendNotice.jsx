// Weekend pause notice.
//
// The engine stops generating NEW signals while the market is shut (Fri 21:00 →
// Sun 21:00 UTC — forex is closed, and SIGNAL_SKIP_CRYPTO_WEEKEND stops crypto too,
// since weekend crypto setups backtest far worse: ~35% win rate vs ~76% on weekdays).
// Without this, a user opens the app on Saturday, sees no new cards, and concludes the
// product is broken.
//
// What it must NOT do is imply everything has stopped: open trades are still evaluated
// through the weekend, and TP / SL / invalidation updates keep arriving in-app and on
// Telegram. That reassurance is the whole point of the notice.
//
// `pause` comes from the feed (SignalFeedView), computed from the same market-window
// function and the same setting the scan uses — so this can never claim signals are
// paused while the engine is still generating them.
//
// Shown once per weekend: the dismissal is keyed by the resume timestamp, so it stays
// closed for this weekend and reappears for the next.
import { useEffect, useState } from "react";

const KEY = "pc_weekend_notice_dismissed";

export default function WeekendNotice({ pause }) {
  const [open, setOpen] = useState(false);
  const resumes = pause?.resumes_at;

  useEffect(() => {
    if (!pause?.paused || !resumes) return;
    if (localStorage.getItem(KEY) === resumes) return;
    setOpen(true);
  }, [pause, resumes]);

  if (!open) return null;

  const close = () => {
    if (resumes) localStorage.setItem(KEY, resumes);
    setOpen(false);
  };

  const resumeText = resumes
    ? new Date(resumes).toLocaleString(undefined, {
        weekday: "long", hour: "numeric", minute: "2-digit",
      })
    : "Sunday evening";

  return (
    <div
      className="reminder-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="weekend-title"
      onClick={close}
    >
      <div className="reminder-card" onClick={(e) => e.stopPropagation()}>
        <button className="reminder-x" aria-label="Close" onClick={close}>✕</button>
        <div className="reminder-icon">🌙</div>
        <h2 id="weekend-title">No new signals this weekend</h2>
        <p className="reminder-text">
          {pause?.crypto_paused
            ? "The engine pauses new signals over the weekend — crypto and forex alike."
            : "Forex markets are closed, so no new forex signals are generated until they reopen."}{" "}
          Weekend sessions are thin and choppy: they produce fakeouts that trip stops, so
          the engine sits them out rather than handing you low-quality setups.
        </p>

        <div className="weekend-still-on">
          <strong>Your open trades are still tracked</strong>
          <ul>
            <li>✅ TP1 / TP2 / TP3 hits — updated as they happen</li>
            <li>🛑 Stop-loss and trend-invalidation alerts</li>
            <li>📲 Telegram trade updates, as normal</li>
          </ul>
        </div>

        <p className="reminder-text">
          New signals resume <strong>{resumeText}</strong>.
        </p>
        <button className="btn-primary btn-block" onClick={close}>Got it</button>
      </div>
    </div>
  );
}
