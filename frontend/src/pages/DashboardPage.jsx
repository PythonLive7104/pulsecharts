// The charting workspace (Section 5). A grid of independent chart panes; the
// sidebar controls act on the active pane. Charts are gated behind login.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useStore, LAYOUTS } from "../store/useStore";
import { useMarketSocket } from "../hooks/useMarketSocket";
import ChartPane from "../components/ChartPane";
import SymbolBar from "../components/SymbolBar";
import DrawingToolbar from "../components/DrawingToolbar";
import IndicatorPicker from "../components/IndicatorPicker";
import Watchlist from "../components/Watchlist";
import Logo from "../components/Logo";
import LayoutPanel from "../components/LayoutPanel";
import ThemeToggle from "../components/ThemeToggle";

const LAYOUT_ICONS = { "1": "▢", "2": "▣▣", "4": "⊞" };

export default function DashboardPage() {
  const initWorkspace = useStore((s) => s.initWorkspace);
  const loadEntitlements = useStore((s) => s.loadEntitlements);
  const isAuthed = useStore((s) => s.isAuthed);
  const entitlements = useStore((s) => s.entitlements);
  const logout = useStore((s) => s.logout);
  const panes = useStore((s) => s.panes);
  const layout = useStore((s) => s.layout);
  const setLayout = useStore((s) => s.setLayout);

  const [alertsUnseen, setAlertsUnseen] = useState(0);

  useEffect(() => {
    initWorkspace();
    loadEntitlements();
  }, [initWorkspace, loadEntitlements]);

  // Poll the unseen-triggered-alert count for the nav badge.
  useEffect(() => {
    if (!isAuthed) return;
    const tick = () => api.alertsUnseen().then((r) => setAlertsUnseen(r.unseen)).catch(() => {});
    tick();
    const id = setInterval(tick, 30000);
    return () => clearInterval(id);
  }, [isAuthed]);

  useMarketSocket();

  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand"><Logo /></Link>
        <SymbolBar />
        <div className="layout-switch">
          {Object.keys(LAYOUTS).map((key) => (
            <button
              key={key}
              className={`tf ${layout === key ? "active" : ""}`}
              title={`${LAYOUTS[key]} chart${LAYOUTS[key] > 1 ? "s" : ""}`}
              onClick={() => setLayout(key)}
            >
              {LAYOUT_ICONS[key]}
            </button>
          ))}
        </div>
        <div className="topbar-right">
          {isAuthed && <Link to="/signals" className="btn-ghost">Signals</Link>}
          {isAuthed && (
            <Link to="/alerts" className="btn-ghost alerts-link">
              Alerts
              {alertsUnseen > 0 && <span className="alerts-badge">{alertsUnseen}</span>}
            </Link>
          )}
          {isAuthed && <Link to="/account/billing" className="btn-ghost">🎁 Refer &amp; earn</Link>}
          <ThemeToggle />
          {isAuthed ? (
            <>
              <Link to="/account" className="plan-pill plan-pill-link">
                {entitlements?.is_premium ? "★ " : ""}{entitlements?.plan_label || "Free"} · Account
              </Link>
              <button className="btn-ghost" onClick={logout}>Sign out</button>
            </>
          ) : (
            <Link to="/login" className="btn-primary">Sign in</Link>
          )}
        </div>
      </header>

      <main className="layout">
        <DrawingToolbar />
        <section className={`chart-grid grid-${layout}`}>
          {panes.map((pane) => (
            <ChartPane key={pane.id} pane={pane} />
          ))}
        </section>
        <aside className="sidebar">
          <IndicatorPicker />
          <LayoutPanel />
          <Watchlist />
        </aside>
      </main>
    </div>
  );
}
