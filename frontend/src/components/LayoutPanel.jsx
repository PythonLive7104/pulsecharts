// Saved chart layouts (Section 8/9/12). Saves the current symbol + timeframe +
// indicator config and reloads it with one click. Free tier is capped at one
// layout (enforced server-side; the error surfaces here). Premium = multiple.
import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store/useStore";

export default function LayoutPanel() {
  const isAuthed = useStore((s) => s.isAuthed);
  const symbols = useStore((s) => s.symbols);
  const activePane = useStore((s) => s.activePane());
  const activeSymbol = activePane?.symbol || null;
  const timeframe = activePane?.timeframe || "1m";
  const currentLayoutConfig = useStore((s) => s.currentLayoutConfig);
  const applyLayout = useStore((s) => s.applyLayout);

  const [layouts, setLayouts] = useState([]);
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  async function refresh() {
    try {
      setLayouts(await api.layouts());
    } catch {
      /* not authed */
    }
  }

  useEffect(() => {
    if (isAuthed) refresh();
    else setLayouts([]);
  }, [isAuthed]);

  async function save() {
    const sym = symbols.find((s) => s.ticker === activeSymbol);
    if (!sym) return;
    const name = window.prompt("Name this layout:", `${activeSymbol} ${timeframe}`);
    if (name === null) return;
    setSaving(true);
    setError(null);
    try {
      await api.saveLayout({
        name: name || `${activeSymbol} ${timeframe}`,
        symbol_id: sym.id,
        timeframe,
        indicator_config: currentLayoutConfig(),
      });
      await refresh();
    } catch (e) {
      setError(e.message); // e.g. free-tier "Saved-layout limit reached"
    } finally {
      setSaving(false);
    }
  }

  function load(layout) {
    const cfg = layout.indicator_config || {};
    applyLayout({
      ticker: layout.symbol?.ticker,
      timeframe: layout.timeframe,
      active: cfg.active,
      params: cfg.params,
      drawings: cfg.drawings,
    });
  }

  async function remove(id, e) {
    e.stopPropagation();
    await api.deleteLayout(id);
    refresh();
  }

  if (!isAuthed) {
    return (
      <div className="panel">
        <h3>Layouts</h3>
        <p className="muted">Sign in to save layouts.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <h3>Layouts</h3>
      <button className="add-btn" onClick={save} disabled={saving || !activeSymbol}>
        {saving ? "Saving…" : "💾 Save current"}
      </button>
      {error && <p className="error">{error}</p>}
      <ul className="watch-list">
        {layouts.map((l) => (
          <li key={l.id} className="watch-item" onClick={() => load(l)} title="Load layout">
            <span>
              {l.name || `${l.symbol?.ticker} ${l.timeframe}`}
              <span className="layout-meta"> · {l.symbol?.ticker} {l.timeframe}</span>
            </span>
            <button onClick={(e) => remove(l.id, e)} title="Delete">×</button>
          </li>
        ))}
        {layouts.length === 0 && <li className="muted">No saved layouts</li>}
      </ul>
    </div>
  );
}
