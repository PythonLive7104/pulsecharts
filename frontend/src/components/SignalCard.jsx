// Trading signal card (Section 19.3). Renders one generated signal: direction,
// confidence, price levels, risk/reward in dollars (per $100 illustrative),
// reasoning, and the not-financial-advice disclaimer.
import { useState } from "react";
import { formatPrice } from "../lib/price";

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
  INVALID: ["Trend changed", "expired"],
};

export default function SignalCard({ s }) {
  const [open, setOpen] = useState(false);
  const buy = s.direction === "BUY";
  const [outLabel, outClass] = OUTCOME[s.outcome] || OUTCOME.PENDING;
  const fmt = (n) => formatPrice(n, s.asset_class, s.symbol);

  return (
    <div className={`signal-card ${buy ? "buy" : "sell"}`}>
      <div className="signal-head">
        <div className="signal-sym">
          <span className="sig-ticker">{s.symbol}</span>
          <span className={`dir-badge ${buy ? "buy" : "sell"}`}>{s.direction}</span>
          <span className="sig-tf">{s.timeframe}</span>
          <span className={`outcome-badge ${outClass}`}>{outLabel}</span>
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
        <div className="level sl"><span>Stop</span><b>{fmt(s.stop_loss)}</b></div>
        <div className="level tp"><span>TP1</span><b>{fmt(s.tp1)}</b></div>
        <div className="level tp"><span>TP2</span><b>{fmt(s.tp2)}</b></div>
        <div className="level tp"><span>TP3</span><b>{fmt(s.tp3)}</b></div>
        <div className="level tp"><span>TP4</span><b>{fmt(s.tp4)}</b></div>
      </div>

      <p className="scaleout-note">
        💡 Suggested: take partial profit at TP1, move your stop to entry (break-even),
        then let the rest run toward TP3/TP4.
      </p>

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
        <span>{s.strategy}</span>
        <span className="muted">{new Date(s.generated_at).toLocaleString()}</span>
      </div>
      <div className="signal-disclaimer">Informational only. Not financial advice.</div>
    </div>
  );
}
