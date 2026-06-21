// Price alerts (Section 12). Create price-cross alerts and see triggered ones.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useStore } from "../store/useStore";
import ThemeToggle from "../components/ThemeToggle";
import Logo from "../components/Logo";

export default function AlertsPage() {
  const logout = useStore((s) => s.logout);
  const entitlements = useStore((s) => s.entitlements);
  const symbols = useStore((s) => s.symbols);
  const loadSymbols = useStore((s) => s.loadSymbols);

  const [alerts, setAlerts] = useState([]);
  const [symbol, setSymbol] = useState("");
  const [condition, setCondition] = useState("above");
  const [price, setPrice] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function refresh() {
    try {
      setAlerts(await api.alerts());
    } catch {
      /* not authed */
    }
  }

  useEffect(() => {
    if (symbols.length === 0) loadSymbols();
    refresh();
    api.markAlertsSeen().catch(() => {}); // visiting clears the unseen badge
  }, [loadSymbols, symbols.length]);

  async function create(e) {
    e.preventDefault();
    setError(null);
    const sym = symbols.find((s) => s.ticker === symbol);
    if (!sym || !price) {
      setError("Pick a symbol and a target price.");
      return;
    }
    setBusy(true);
    try {
      await api.createAlert(sym.id, condition, Number(price));
      setPrice("");
      await refresh();
    } catch (e2) {
      setError(e2.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(id) {
    await api.deleteAlert(id);
    refresh();
  }

  const active = alerts.filter((a) => a.is_active);
  const triggered = alerts.filter((a) => !a.is_active);

  return (
    <div className="alerts-page">
      <header className="topbar">
        <Link to="/" className="brand"><Logo /></Link>
        <nav className="signals-nav">
          <Link to="/app" className="btn-ghost">Charts</Link>
          <span className="active-tab">Alerts</span>
        </nav>
        <div className="topbar-right">
          <ThemeToggle />
          <Link to="/account" className="plan-pill plan-pill-link">
            {entitlements?.is_premium ? "★ Premium" : "Free"} · Account
          </Link>
          <button className="btn-ghost" onClick={logout}>Sign out</button>
        </div>
      </header>

      <main className="alerts-body">
        <form className="alert-form card" onSubmit={create}>
          <h2>New price alert</h2>
          <div className="alert-fields">
            <label>
              Symbol
              <select value={symbol} onChange={(e) => setSymbol(e.target.value)}>
                <option value="">Select…</option>
                {symbols.map((s) => (
                  <option key={s.id} value={s.ticker}>{s.ticker}</option>
                ))}
              </select>
            </label>
            <label>
              Condition
              <select value={condition} onChange={(e) => setCondition(e.target.value)}>
                <option value="above">Crosses above</option>
                <option value="below">Crosses below</option>
              </select>
            </label>
            <label>
              Target price
              <input type="number" step="any" value={price}
                onChange={(e) => setPrice(e.target.value)} placeholder="e.g. 70000" />
            </label>
          </div>
          {error && <p className="error">{error}</p>}
          <button className="btn-primary" disabled={busy}>{busy ? "Creating…" : "Create alert"}</button>
        </form>

        <section className="alert-lists">
          <h2>Active alerts</h2>
          {active.length === 0 && <p className="muted">No active alerts.</p>}
          {active.map((a) => (
            <div key={a.id} className="alert-row">
              <span><b>{a.symbol}</b> {a.condition === "above" ? "↑ above" : "↓ below"} {a.target_price}</span>
              <button className="alert-del" onClick={() => remove(a.id)}>×</button>
            </div>
          ))}

          {triggered.length > 0 && (
            <>
              <h2>Triggered</h2>
              {triggered.map((a) => (
                <div key={a.id} className="alert-row triggered">
                  <span>
                    🔔 <b>{a.symbol}</b> {a.condition === "above" ? "crossed above" : "crossed below"} {a.target_price}
                    <span className="muted"> · hit {a.triggered_price} · {new Date(a.triggered_at).toLocaleString()}</span>
                  </span>
                  <button className="alert-del" onClick={() => remove(a.id)}>×</button>
                </div>
              ))}
            </>
          )}
        </section>
      </main>
    </div>
  );
}
