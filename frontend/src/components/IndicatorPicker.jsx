// Indicator picker with premium gating (Section 5, 11) + per-indicator params.
// Unlocked/locked state comes from /api/me/entitlements/. Active indicators with
// tunable parameters show a ⚙ that expands inline inputs (period, mult, …).
import { useState } from "react";
import { useStore } from "../store/useStore";
import { PARAM_SCHEMA } from "../lib/indicators";

// Every indicator in the catalog now has client-side math (lib/indicators.js).
const IMPLEMENTED = new Set([
  "sma", "ema", "volume", "rsi", "macd", "bbands", "stoch", "atr", "fib", "vwap", "ichimoku",
]);

function ParamEditor({ slug }) {
  const schema = PARAM_SCHEMA[slug] || [];
  const activePane = useStore((s) => s.activePane());
  const params = activePane?.params?.[slug] || {};
  const setIndicatorParam = useStore((s) => s.setIndicatorParam);
  if (schema.length === 0 || !activePane) return null;

  return (
    <div className="indicator-params" onClick={(e) => e.stopPropagation()}>
      {schema.map((p) => (
        <label key={p.key} className="param">
          <span>{p.label}</span>
          <input
            type="number"
            min={p.min}
            max={p.max}
            step={p.step || 1}
            value={params[p.key] ?? p.default}
            onChange={(e) => {
              const v = e.target.value === "" ? p.default : Number(e.target.value);
              setIndicatorParam(activePane.id, slug, p.key, v);
            }}
          />
        </label>
      ))}
    </div>
  );
}

export default function IndicatorPicker() {
  const entitlements = useStore((s) => s.entitlements);
  const activePane = useStore((s) => s.activePane());
  const activeIndicators = activePane?.indicators || new Set();
  const toggleIndicator = useStore((s) => s.toggleIndicator);
  const [expanded, setExpanded] = useState(null); // slug whose params are open

  if (!entitlements) {
    return (
      <div className="panel">
        <h3>Indicators</h3>
        <p className="muted">Sign in to manage indicators.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h3>Indicators</h3>
      <ul className="indicator-list">
        {entitlements.indicators.map((ind) => {
          const active = activeIndicators.has(ind.slug);
          const ready = IMPLEMENTED.has(ind.slug);
          const locked = !ind.unlocked;
          const hasParams = (PARAM_SCHEMA[ind.slug] || []).length > 0;
          return (
            <li key={ind.slug} className="indicator-row">
              <div
                className={`indicator ${locked ? "locked" : ""} ${active ? "active" : ""}`}
                onClick={() => !locked && ready && activePane && toggleIndicator(activePane.id, ind.slug)}
                title={locked ? "Premium — upgrade to unlock" : "Toggle"}
              >
                <span>{ind.label}</span>
                <span className="indicator-tags">
                  {active && hasParams && (
                    <button
                      className="gear"
                      title="Settings"
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpanded(expanded === ind.slug ? null : ind.slug);
                      }}
                    >
                      ⚙
                    </button>
                  )}
                  <span className="tag">{locked ? "🔒" : active ? "on" : ""}</span>
                </span>
              </div>
              {active && expanded === ind.slug && <ParamEditor slug={ind.slug} />}
            </li>
          );
        })}
      </ul>
      {!entitlements.is_premium && (
        <button className="upgrade-btn" onClick={() => alert("Upgrade flow — billing coming soon (Section 16)")}>
          Upgrade to Premium
        </button>
      )}
    </div>
  );
}
