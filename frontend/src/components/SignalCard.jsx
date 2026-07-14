// Trading signal card (Section 19.3). Renders one generated signal: direction,
// confidence, price levels, risk/reward in dollars (per $100 illustrative),
// reasoning, and the not-financial-advice disclaimer.
import { useState } from "react";
import { formatPrice } from "../lib/price";
import { timeAgo, fullTime } from "../lib/time";

// Illustrative position size for the $ figures. Derived from the signal's
// scale-invariant risk/reward percentages so the display is always consistent —
// signals store their own dollar_* at generation time, which can be stale if the
// size changed, so we never read those. Change this one number to rescale.
const TRADE_SIZE = 100;
const dollars = (pct) => (pct == null ? "—" : `$${((Number(pct) / 100) * TRADE_SIZE).toFixed(2)}`);

// outcome -> {label, css class}
const OUTCOME = {
  PENDING: ["Active", "pending"],
  TP1: ["✓ TP1", "win"], TP2: ["✓ TP2", "win"], TP3: ["✓ TP3", "win"], TP4: ["✓ TP4", "win"],
  SL: ["✕ Stopped", "loss"], EXPIRED: ["Expired", "expired"],
  // Trend flipped before TP/SL — closed flat (0 P/L), not a loss. Neutral styling.
  INVALID: ["Invalidated", "closed"],
};

export default function SignalCard({ s }) {
  const [open, setOpen] = useState(false);
  const buy = s.direction === "BUY";
  let [outLabel, outClass] = OUTCOME[s.outcome] || OUTCOME.PENDING;
  const fmt = (n) => formatPrice(n, s.asset_class, s.symbol);
  // Targets already tagged. An open trade keeps running after TP1/TP2 (§19.2 —
  // it resolves only at TP3 or the breakeven stop), so "Active" alone hides the
  // fact that the user's partial is due and their stop should be at entry.
  const reached = s.best_tp || 0;
  const running = s.outcome === "PENDING" && reached > 0;
  if (running) {
    outLabel = `Running · TP${reached} ✓`;
    outClass = "running";
  }
  // Confluence: how many distinct strategies agree on this call (>= 2 is the
  // headline reliability signal). Falls back to the single generating strategy.
  const agree = s.confluence_services?.length ? s.confluence_services : [s.strategy];
  const nAgree = s.confluence_count || agree.length;

  return (
    <div className={`signal-card ${buy ? "buy" : "sell"}`}>
      <div className="signal-head">
        <div className="signal-sym">
          <span className="sig-ticker">{s.symbol}</span>
          <span className={`dir-badge ${buy ? "buy" : "sell"}`}>{s.direction}</span>
          <span className="sig-tf">{s.timeframe}</span>
          <span className={`outcome-badge ${outClass}`}>{outLabel}</span>
          {nAgree >= 2 && (
            <span
              className="confluence-badge"
              title={`Confluence — ${nAgree} strategies agree on this ${s.direction}: ${agree.join(", ")}`}
            >
              📊 {nAgree} agree
            </span>
          )}
        </div>
        <div
          className="confidence"
          title="Conviction — how strongly the indicators align for this setup. Not a win rate; see Realized accuracy for that."
        >
          <span className="conf-label">Conviction</span>
          <div className="conf-bar"><div className="conf-fill" style={{ width: `${s.confidence_pct}%` }} /></div>
          <span className="conf-pct">{s.confidence_pct}%</span>
        </div>
      </div>

      <div className="levels">
        <div className="level"><span>Entry</span><b>{fmt(s.entry_price)}</b></div>
        <div className="level sl">
          <span>Stop</span>
          <b>{running ? `${fmt(s.entry_price)} (BE)` : fmt(s.stop_loss)}</b>
        </div>
        {[s.tp1, s.tp2, s.tp3, s.tp4].map((tp, i) => tp == null ? null : (
          <div key={i} className={`level tp${reached >= i + 1 ? " hit" : ""}`}>
            <span>TP{i + 1}{reached >= i + 1 ? " ✓" : ""}</span>
            <b>{fmt(tp)}</b>
          </div>
        ))}
      </div>

      {running ? (
        <p className="scaleout-note running">
          🎯 TP{reached} tagged — partial{reached > 1 ? "s" : ""} banked, stop moved to entry
          (break-even). Trade still open; runner targets TP3 {fmt(s.tp3)}.
        </p>
      ) : (
        <p className="scaleout-note">
          💡 Suggested: take partial profit at TP1, move your stop to entry (break-even),
          then let the rest run toward TP3.
        </p>
      )}

      <div className="rr-line">
        Risk <b className="risk">{dollars(s.risk_pct)}</b> → make{" "}
        <b className="reward">{dollars(s.reward_tp2_pct)}</b> at TP2 (1:{Number(s.risk_reward_tp2).toFixed(1)})
        <span className="rr-note"> · per ${TRADE_SIZE} traded (illustrative)</span>
      </div>

      {s.reasoning && (
        <div className="reasoning">
          <button className="reason-toggle" onClick={() => setOpen((o) => !o)}>
            {open ? "Hide" : "Why this signal?"}
          </button>
          {open && (
            <div className="reason-body">
              <p>{s.reasoning}</p>
              {s.invalidation && <p className="invalidation"><b>Invalidation:</b> {s.invalidation}</p>}
            </div>
          )}
        </div>
      )}

      <div className="signal-foot">
        <span className="sig-strategies">{nAgree >= 2 ? agree.join(" + ") : s.strategy}</span>
        {/* Freshness is what the reader actually wants here; the exact timestamp is a
            hover away for anyone lining the signal up against a chart. */}
        <time className="sig-time muted" dateTime={s.generated_at} title={fullTime(s.generated_at)}>
          {timeAgo(s.generated_at)}
        </time>
      </div>
      <div className="signal-disclaimer">Informational only. Not financial advice.</div>
    </div>
  );
}
