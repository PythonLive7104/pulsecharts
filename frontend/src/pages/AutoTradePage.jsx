// Auto-Trade page (Pro feature, Section 13 follow-on). Lets a Pro user connect a
// Bybit broker and set a risk envelope so signals are executed on their own
// account automatically. Non-Pro users see a locked upgrade pitch — the whole
// point of this page for them is to discover it's a Pro feature.
//
// Execution itself is gated server-side by AUTO_TRADE_ENABLED (off until accuracy
// + legal review are done). Connecting/configuring is allowed ahead of that so
// users can set up early; we show an honest "not live yet" banner meanwhile.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useStore } from "../store/useStore";
import ThemeToggle from "../components/ThemeToggle";
import Logo from "../components/Logo";

const SIZING_LABELS = {
  risk_pct: "Risk % of balance",
  fixed_usd: "Fixed USD notional",
  pct_balance: "% of balance notional",
};

export default function AutoTradePage() {
  const logout = useStore((s) => s.logout);
  const entitlements = useStore((s) => s.entitlements);
  const loadEntitlements = useStore((s) => s.loadEntitlements);

  const isPro = Boolean(entitlements?.auto_trade);
  const isLive = Boolean(entitlements?.auto_trade_live);

  const [broker, setBroker] = useState(null);
  const [config, setConfig] = useState(null);
  const [executions, setExecutions] = useState([]);
  const [loading, setLoading] = useState(true);

  // Broker-connect form.
  const [form, setForm] = useState({ api_key: "", api_secret: "", testnet: true, authorize: false });
  const [connectErr, setConnectErr] = useState(null);
  const [connecting, setConnecting] = useState(false);

  const [saveMsg, setSaveMsg] = useState(null);

  async function load() {
    setLoading(true);
    try {
      const [b, c, ex] = await Promise.all([
        api.brokerStatus(),
        api.autoTradeConfig(),
        api.autoTradeExecutions().catch(() => []),
      ]);
      setBroker(b);
      setConfig(c);
      setExecutions(Array.isArray(ex) ? ex : []);
    } catch {
      /* not pro / offline — the gate below handles it */
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadEntitlements();
    if (isPro) load();
    else setLoading(false);
  }, [isPro, loadEntitlements]);

  async function connect(e) {
    e.preventDefault();
    setConnectErr(null);
    setConnecting(true);
    try {
      await api.brokerConnect(form.api_key.trim(), form.api_secret.trim(), form.testnet, form.authorize);
      setForm({ api_key: "", api_secret: "", testnet: true, authorize: false });
      await load();
    } catch (err) {
      setConnectErr(err.message);
    } finally {
      setConnecting(false);
    }
  }

  async function disconnect() {
    if (!confirm("Disconnect your broker and delete the stored API key?")) return;
    await api.brokerDisconnect();
    await load();
  }

  function patchConfig(patch) {
    setConfig((c) => ({ ...c, ...patch }));
  }

  async function saveConfig(extra = {}) {
    setSaveMsg(null);
    try {
      const payload = {
        enabled: config.enabled,
        sizing_mode: config.sizing_mode,
        risk_pct: Number(config.risk_pct),
        fixed_usd: Number(config.fixed_usd),
        pct_balance: Number(config.pct_balance),
        leverage: Number(config.leverage),
        max_open_positions: Number(config.max_open_positions),
        max_daily_trades: Number(config.max_daily_trades),
        max_slippage_pct: Number(config.max_slippage_pct),
        min_confidence: Number(config.min_confidence),
        ...extra,
      };
      const saved = await api.saveAutoTradeConfig(payload);
      setConfig(saved);
      setSaveMsg("Saved.");
    } catch (err) {
      setSaveMsg(err.message);
    }
  }

  async function toggleEnabled() {
    const next = !config.enabled;
    patchConfig({ enabled: next });
    await saveConfig({ enabled: next });
  }

  async function panic() {
    if (!confirm("Disable auto-trade and close all open positions now?")) return;
    const res = await api.autoTradePanic();
    setSaveMsg(res.detail || "Auto-trade disabled.");
    await load();
  }

  const topbar = (
    <header className="topbar">
      <Link to="/" className="brand"><Logo /></Link>
      <nav className="signals-nav">
        <Link to="/app" className="btn-ghost">Charts</Link>
        <Link to="/signals" className="btn-ghost">Signals</Link>
        <span className="active-tab">Auto-Trade</span>
      </nav>
      <div className="topbar-right">
        <ThemeToggle />
        <Link to="/account" className="plan-pill plan-pill-link">
          {entitlements?.is_premium ? "★ " : ""}{entitlements?.plan_label || "Free"} · Account
        </Link>
        <button className="btn-ghost" onClick={logout}>Sign out</button>
      </div>
    </header>
  );

  // --- non-Pro: discovery / upgrade gate ---
  if (!isPro) {
    return (
      <div className="signals-page">
        {topbar}
        <main className="signals-body">
          <div className="signals-locked">
            <div className="lock-card">
              <div className="lock-icon">🤖</div>
              <h1>Auto-Trade <span className="feature-tag premium">Pro</span></h1>
              <p className="muted">
                Connect your Bybit account and let high-confidence signals execute automatically —
                with your own risk envelope: position sizing, leverage, daily caps, stop-loss and
                scaled take-profits, plus a one-tap kill switch. Available on the <strong>Pro</strong> plan.
              </p>
              <Link to="/account/billing" className="btn-primary btn-lg">Upgrade to Pro</Link>
              <p className="muted at-disclaimer">
                Automated trading carries risk. Signals are algorithmic, informational output —
                not financial advice. You authorize and control every aspect of execution.
              </p>
            </div>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="signals-page">
      {topbar}
      <main className="signals-body at-body">
        <div className="at-main">
          <div className="at-head">
            <h1>Auto-Trade <span className="feature-tag premium">Pro</span></h1>
            <p className="muted">
              Signals from the strategies you follow, on the coins you watch, executed on your own
              Bybit account.
            </p>
          </div>

          {!isLive && (
            <div className="at-banner">
              <strong>Setup is open — execution isn’t live yet.</strong>
              <span className="muted">
                You can connect your broker and tune your risk settings now. No order is placed on
                your account until we switch execution on after final accuracy and compliance review.
              </span>
            </div>
          )}

          {loading && <p className="muted">Loading…</p>}

          {/* --- broker connection --- */}
          {!loading && (
            <section className="at-panel">
              <h3>Broker connection</h3>
              {broker?.connected ? (
                <div className="at-broker-connected">
                  <div>
                    <strong>Bybit{broker.testnet ? " (testnet)" : ""}</strong>
                    <span className={`at-status at-status-${broker.status}`}>{broker.status}</span>
                    {broker.permission_verified && <span className="muted"> · trade-only key verified</span>}
                    {broker.last_error && <p className="error">{broker.last_error}</p>}
                  </div>
                  <button className="btn-ghost" onClick={disconnect}>Disconnect</button>
                </div>
              ) : (
                <form className="at-connect" onSubmit={connect}>
                  <p className="muted">
                    Create a Bybit API key with <strong>Trade</strong> enabled and{" "}
                    <strong>Withdrawal disabled</strong>. We verify the key is trade-only before
                    storing it (encrypted). We can never move funds off your account.
                  </p>
                  <label>API key
                    <input value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })}
                      autoComplete="off" required />
                  </label>
                  <label>API secret
                    <input type="password" value={form.api_secret}
                      onChange={(e) => setForm({ ...form, api_secret: e.target.value })}
                      autoComplete="off" required />
                  </label>
                  <label className="at-check">
                    <input type="checkbox" checked={form.testnet}
                      onChange={(e) => setForm({ ...form, testnet: e.target.checked })} />
                    Use testnet (recommended until you’ve seen it work)
                  </label>
                  <label className="at-check">
                    <input type="checkbox" checked={form.authorize}
                      onChange={(e) => setForm({ ...form, authorize: e.target.checked })} required />
                    I authorize automated trade execution on my own account.
                  </label>
                  {connectErr && <p className="error">{connectErr}</p>}
                  <button className="btn-primary" disabled={connecting}>
                    {connecting ? "Verifying…" : "Connect & verify"}
                  </button>
                </form>
              )}
            </section>
          )}

          {/* --- risk config --- */}
          {!loading && config && (
            <section className="at-panel">
              <div className="at-panel-head">
                <h3>Risk settings</h3>
                <label className="at-switch">
                  <input type="checkbox" checked={config.enabled} onChange={toggleEnabled}
                    disabled={!broker?.connected} />
                  <span>{config.enabled ? "Auto-trade ON" : "Auto-trade OFF"}</span>
                </label>
              </div>
              {!broker?.connected && (
                <p className="muted">Connect a verified broker above to enable auto-trade.</p>
              )}

              <div className="at-grid">
                <label>Position sizing
                  <select value={config.sizing_mode}
                    onChange={(e) => patchConfig({ sizing_mode: e.target.value })}>
                    {Object.entries(SIZING_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </label>
                {config.sizing_mode === "risk_pct" && (
                  <label>Risk % per trade
                    <input type="number" step="0.1" min="0.1" max="20" value={config.risk_pct}
                      onChange={(e) => patchConfig({ risk_pct: e.target.value })} />
                  </label>
                )}
                {config.sizing_mode === "fixed_usd" && (
                  <label>Fixed notional (USD)
                    <input type="number" step="1" min="1" value={config.fixed_usd}
                      onChange={(e) => patchConfig({ fixed_usd: e.target.value })} />
                  </label>
                )}
                {config.sizing_mode === "pct_balance" && (
                  <label>% of balance
                    <input type="number" step="0.5" min="0.5" value={config.pct_balance}
                      onChange={(e) => patchConfig({ pct_balance: e.target.value })} />
                  </label>
                )}
                <label>Leverage
                  <input type="number" step="1" min="1" max="50" value={config.leverage}
                    onChange={(e) => patchConfig({ leverage: e.target.value })} />
                </label>
                <label>Max open positions
                  <input type="number" step="1" min="1" value={config.max_open_positions}
                    onChange={(e) => patchConfig({ max_open_positions: e.target.value })} />
                </label>
                <label>Max trades / day
                  <input type="number" step="1" min="1" value={config.max_daily_trades}
                    onChange={(e) => patchConfig({ max_daily_trades: e.target.value })} />
                </label>
                <label>Max slippage %
                  <input type="number" step="0.1" min="0" value={config.max_slippage_pct}
                    onChange={(e) => patchConfig({ max_slippage_pct: e.target.value })} />
                </label>
                <label>Min confidence (0 = plan default)
                  <input type="number" step="1" min="0" max="100" value={config.min_confidence}
                    onChange={(e) => patchConfig({ min_confidence: e.target.value })} />
                </label>
              </div>

              <div className="at-actions">
                <button className="btn-primary" onClick={() => saveConfig()}>Save settings</button>
                <button className="btn-ghost at-panic" onClick={panic}>⛔ Kill switch</button>
                {saveMsg && <span className="muted">{saveMsg}</span>}
              </div>
            </section>
          )}

          {/* --- executions --- */}
          {!loading && (
            <section className="at-panel">
              <h3>Recent executions</h3>
              {executions.length === 0 ? (
                <p className="muted">No executions yet. They’ll appear here once signals are placed.</p>
              ) : (
                <div className="at-exec-table">
                  <div className="at-exec-row at-exec-head">
                    <span>Symbol</span><span>Side</span><span>Status</span><span>Qty</span>
                    <span>Entry</span><span>PnL</span><span>When</span>
                  </div>
                  {executions.map((ex) => (
                    <div key={ex.id} className="at-exec-row">
                      <span>{ex.symbol}</span>
                      <span className={ex.side === "Buy" ? "win" : "loss"}>{ex.side || "—"}</span>
                      <span className={`at-exstatus at-exstatus-${ex.status}`}>{ex.status}</span>
                      <span>{ex.qty ?? "—"}</span>
                      <span>{ex.fill_price ?? ex.intended_entry ?? "—"}</span>
                      <span className={ex.realized_pnl > 0 ? "win" : ex.realized_pnl < 0 ? "loss" : ""}>
                        {ex.realized_pnl != null ? ex.realized_pnl.toFixed(2) : "—"}
                      </span>
                      <span className="muted">{new Date(ex.created_at).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              )}
            </section>
          )}

          <p className="feed-disclaimer">
            Automated trading carries risk and can lose money. Signals are algorithmic,
            informational output — not financial advice. You authorize and control execution;
            disable it any time with the kill switch.
          </p>
        </div>
      </main>
    </div>
  );
}
