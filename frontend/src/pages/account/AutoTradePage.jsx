// Auto-Trade — connect a Bybit account and let the executor place bracket orders
// from the signals you already follow. Premium-gated (like Telegram delivery); the
// executor itself is also globally gated server-side (AUTO_TRADE_ENABLED), so this
// page configures intent even before the feature is switched on for the platform.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../../api";
import { useStore } from "../../store/useStore";

const STATUS_LABEL = {
  open: "Open",
  closed: "Closed",
  liquidated: "Liquidated",
  skipped: "Skipped",
  rejected: "Rejected",
};

export default function AutoTradePage() {
  const entitlements = useStore((s) => s.entitlements);
  const loadEntitlements = useStore((s) => s.loadEntitlements);
  const [broker, setBroker] = useState(null);
  const [loading, setLoading] = useState(true);

  const reload = () => api.broker().then(setBroker).catch(() => setBroker(null));

  useEffect(() => {
    loadEntitlements();
  }, [loadEntitlements]);

  const isPremium = entitlements?.is_premium;

  // Only fetch broker state once we know the user is paid — the endpoint is Starter/
  // Pro-only (403 for Free), so free users see the upsell without a failed request.
  useEffect(() => {
    if (entitlements == null) return; // still loading entitlements
    if (!isPremium) return setLoading(false);
    reload().finally(() => setLoading(false));
  }, [entitlements, isPremium]);

  return (
    <div className="account-pages">
      <h1>Auto-Trade</h1>
      <p className="muted">
        Automatically place trades on your own Bybit account from the signals you follow —
        entry, stop-loss and take-profit targets. Each trade commits a <b>fixed margin</b>{" "}
        you set (default 1 USDT), not a percentage of your balance; leverage is chosen
        automatically so liquidation stays beyond the stop. Your total exposure is roughly
        that margin × your max open positions.
      </p>

      <div className="at-disclaimer">
        <strong>⚠️ This places real orders with real money.</strong>
        <span>
          Auto-trading is high risk. Signals are informational algorithmic output, <b>not
          financial advice</b>. Start on <b>Testnet</b>, use API keys with trade permission
          but <b>no withdrawal access</b>, and never commit funds you can't afford to lose.
          You are solely responsible for every trade placed.
        </span>
      </div>

      {loading ? (
        <div className="card"><p className="muted">Loading…</p></div>
      ) : !isPremium ? (
        <div className="card at-upsell">
          <h2>Included on Starter &amp; Pro</h2>
          <p className="muted">
            Auto-trade is a premium feature. Upgrade to connect your Bybit account and have
            signals from your followed strategies placed automatically.
          </p>
          <Link to="/account/billing" className="btn-primary">Upgrade</Link>
        </div>
      ) : (
        <>
          <ConnectionCard broker={broker} onChange={reload} />
          {broker?.connected && (
            <>
              <EnableCard broker={broker} onChange={reload} />
              <RiskEnvelopeCard broker={broker} onChange={reload} />
            </>
          )}
          <TradeHistoryCard />
        </>
      )}
    </div>
  );
}

// --- broker connection (API keys) -----------------------------------------
function ConnectionCard({ broker, onChange }) {
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [testnet, setTestnet] = useState(broker?.testnet ?? true);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const [test, setTest] = useState(null);

  const connected = broker?.connected;

  async function save(e) {
    e.preventDefault();
    setErr(null); setMsg(null); setTest(null);
    if (!apiKey || !apiSecret) return setErr("Enter both your API key and secret.");
    // Browsers have been seen autofilling the saved site login into these fields; an
    // email is never a Bybit key, so catch it here rather than storing a dud credential
    // the user only discovers when a trade fails to place.
    if (apiKey.includes("@") || apiSecret.includes("@")) {
      return setErr("That looks like an email address, not a Bybit API key — your browser " +
                    "may have autofilled it. Paste the key and secret from Bybit's API page.");
    }
    setBusy(true);
    try {
      await api.saveBroker({ api_key: apiKey, api_secret: apiSecret, testnet });
      setApiKey(""); setApiSecret("");
      setMsg("Keys saved securely.");
      onChange();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    setErr(null); setMsg(null); setTest(null);
    setBusy(true);
    try {
      const r = await api.testBroker();
      setTest(r);
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    if (!confirm("Disconnect your Bybit account and delete the stored keys?")) return;
    setBusy(true);
    try {
      await api.disconnectBroker();
      setTest(null);
      onChange();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>
        Bybit connection
        {connected && (
          <span className={`at-badge ${broker.testnet ? "at-testnet" : "at-mainnet"}`}>
            {broker.testnet ? "Testnet" : "Mainnet"}
          </span>
        )}
      </h2>

      {connected ? (
        <>
          <div className="field-row">
            <span className="field-label">Status</span>
            <span className="at-connected">✓ Connected — keys on file</span>
          </div>
          <div className="at-actions">
            <button className="btn-ghost" onClick={runTest} disabled={busy}>
              {busy ? "Testing…" : "Test connection"}
            </button>
            <button className="btn-ghost at-danger" onClick={disconnect} disabled={busy}>
              Disconnect
            </button>
          </div>
          {test?.ok && (
            <>
              <p className="success">
                Connection OK — {test.testnet ? "Testnet" : "Mainnet"} USDT equity:{" "}
                <b>{Number(test.usdt_equity).toLocaleString()} USDT</b>
              </p>
              {/* Cross margin means a losing trade can draw on the whole balance, not
                  just its own margin — worth showing before the first trade, not after. */}
              <p className={test.isolated ? "muted" : "error"}>
                {test.isolated
                  ? "Margin mode: Isolated — each trade's loss is capped at its own margin."
                  : "Margin mode: Cross — losses are NOT capped per trade. Auto-trade will " +
                    "switch this account to Isolated before placing its first order."}
              </p>
            </>
          )}
          <details className="at-rekey">
            <summary>Replace API keys</summary>
            <KeyForm {...{ apiKey, setApiKey, apiSecret, setApiSecret, testnet, setTestnet, save, busy }} />
          </details>
        </>
      ) : (
        <>
          <p className="muted">
            Create an API key in Bybit (API Management) with <b>Contract — Orders &amp; Positions</b>{" "}
            permission and <b>no withdrawal</b> rights. Keys are encrypted at rest and never shown again.
          </p>
          <KeyForm {...{ apiKey, setApiKey, apiSecret, setApiSecret, testnet, setTestnet, save, busy }} />
        </>
      )}
      {err && <p className="error">{err}</p>}
      {msg && <p className="success">{msg}</p>}
    </div>
  );
}

function KeyForm({ apiKey, setApiKey, apiSecret, setApiSecret, testnet, setTestnet, save, busy }) {
  return (
    <form onSubmit={save} className="auth-form">
      {/* autoComplete="new-password" (not "off", which Chrome ignores) + non-login-ish
          names stop the password manager treating text+password as a sign-in form and
          autofilling the user's site email/password into the key fields. */}
      <label>API key
        <input name="bybit-key" value={apiKey} onChange={(e) => setApiKey(e.target.value)}
               autoComplete="new-password" data-lpignore="true" data-form-type="other"
               spellCheck={false} placeholder="Bybit API key" />
      </label>
      <label>API secret
        <input type="password" name="bybit-secret" value={apiSecret}
               onChange={(e) => setApiSecret(e.target.value)}
               autoComplete="new-password" data-lpignore="true" data-form-type="other"
               spellCheck={false} placeholder="Bybit API secret" />
      </label>
      <label className="at-checkbox">
        <input type="checkbox" checked={testnet} onChange={(e) => setTestnet(e.target.checked)} />
        Testnet account (recommended while testing — no real funds)
      </label>
      <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save keys"}</button>
    </form>
  );
}

// --- master enable switch --------------------------------------------------
function EnableCard({ broker, onChange }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);

  async function toggle() {
    setErr(null);
    setBusy(true);
    try {
      await api.saveBroker({ enabled: !broker.enabled });
      onChange();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Auto-trading</h2>
      <div className="field-row">
        <span className="field-label">
          {broker.enabled ? "On — signals are placed automatically" : "Off — no orders are placed"}
        </span>
        <button
          className={`at-switch ${broker.enabled ? "on" : ""}`}
          onClick={toggle}
          disabled={busy}
          role="switch"
          aria-checked={broker.enabled}
        >
          <span className="at-knob" />
        </button>
      </div>
      <p className="muted">
        Only signals from strategies you follow, on coins in your watchlist, that clear the
        confluence threshold are traded — the same ones sent to your Telegram.
      </p>
      {err && <p className="error">{err}</p>}
    </div>
  );
}

// --- per-user risk envelope ------------------------------------------------
function RiskEnvelopeCard({ broker, onChange }) {
  const [margin, setMargin] = useState(broker.margin_per_trade_usd ?? 1);
  const [leverage, setLeverage] = useState(broker.max_leverage ?? 10);
  const [maxOpen, setMaxOpen] = useState(broker.max_open_positions ?? "");
  const [maxDaily, setMaxDaily] = useState(broker.max_daily_trades ?? "");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);

  async function save(e) {
    e.preventDefault();
    setErr(null); setMsg(null);
    setBusy(true);
    try {
      await api.saveBroker({
        margin_per_trade_usd: Number(margin),
        max_leverage: Number(leverage),
        // Blank = "use the platform default" (null).
        max_open_positions: maxOpen === "" ? null : Number(maxOpen),
        max_daily_trades: maxDaily === "" ? null : Number(maxDaily),
      });
      setMsg("Risk settings saved.");
      onChange();
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Risk settings</h2>
      <form onSubmit={save} className="auth-form at-grid">
        <label>Margin per trade (USDT)
          <input type="number" min="0.1" step="0.1" value={margin} onChange={(e) => setMargin(e.target.value)} />
          <small className="muted">Fixed margin committed per position. Small keeps room for several trades.</small>
        </label>
        <label>Max leverage
          <input type="number" min="1" max="100" step="1" value={leverage} onChange={(e) => setLeverage(e.target.value)} />
          <small className="muted">Ceiling on leverage the sizer may use (caps liquidation risk).</small>
        </label>
        <label>Max open positions
          <input type="number" min="0" step="1" value={maxOpen} placeholder={`Default (${broker.effective_max_open_positions})`} onChange={(e) => setMaxOpen(e.target.value)} />
          <small className="muted">Blank = platform default.</small>
        </label>
        <label>Max trades per day
          <input type="number" min="0" step="1" value={maxDaily} placeholder={`Default (${broker.effective_max_daily_trades})`} onChange={(e) => setMaxDaily(e.target.value)} />
          <small className="muted">Blank = platform default.</small>
        </label>
        <div className="at-grid-full">
          {err && <p className="error">{err}</p>}
          {msg && <p className="success">{msg}</p>}
          <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Save risk settings"}</button>
        </div>
      </form>
    </div>
  );
}

// --- recent executions -----------------------------------------------------
function TradeHistoryCard() {
  const [trades, setTrades] = useState(null);

  useEffect(() => {
    api.trades().then(setTrades).catch(() => setTrades([]));
  }, []);

  return (
    <div className="card">
      <h2>Recent trades</h2>
      {trades === null ? (
        <p className="muted">Loading…</p>
      ) : trades.length === 0 ? (
        <p className="muted">No auto-trades yet. Once auto-trading is on, placed orders show here.</p>
      ) : (
        <div className="at-table-wrap">
          <table className="at-table">
            <thead>
              <tr>
                <th>When</th><th>Symbol</th><th>Side</th><th>Status</th>
                <th>Lev</th><th>Entry</th><th>Stop</th><th>Notes</th>
              </tr>
            </thead>
            <tbody>
              {trades.map((t) => (
                <tr key={t.id}>
                  <td>{new Date(t.created_at).toLocaleString()}</td>
                  <td>{t.bybit_symbol}</td>
                  <td className={t.direction === "BUY" ? "at-buy" : "at-sell"}>{t.direction}</td>
                  <td>
                    <span className={`at-status at-st-${t.status}`}>{STATUS_LABEL[t.status] || t.status}</span>
                    {t.scaleout && t.status === "open" && (
                      <span className="at-tag">{t.breakeven_moved ? "at breakeven" : "scale-out"}</span>
                    )}
                  </td>
                  <td>{t.leverage ? `${t.leverage}×` : "—"}</td>
                  <td>{t.entry_price || "—"}</td>
                  <td>{t.stop_loss || "—"}</td>
                  <td className="at-reason">{t.reason || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
