// Watchlist panel (Section 5, 12 — tier-capped server-side).
import { useEffect, useState } from "react";
import { api } from "../api";
import { useStore } from "../store/useStore";

export default function Watchlist() {
  const isAuthed = useStore((s) => s.isAuthed);
  const activePane = useStore((s) => s.activePane());
  const activeSymbol = activePane?.symbol || null;
  const symbols = useStore((s) => s.symbols);
  const selectSymbol = useStore((s) => s.selectSymbol);

  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);

  async function refresh() {
    try {
      setItems(await api.watchlist());
    } catch {
      /* not authed yet */
    }
  }

  useEffect(() => {
    if (isAuthed) refresh();
    else setItems([]);
  }, [isAuthed]);

  async function add() {
    const sym = symbols.find((s) => s.ticker === activeSymbol);
    if (!sym) return;
    try {
      setError(null);
      await api.addWatch(sym.id);
      refresh();
    } catch (e) {
      setError(e.message); // e.g. "Watchlist limit reached" / "already in watchlist"
    }
  }

  async function remove(id) {
    await api.removeWatch(id);
    refresh();
  }

  if (!isAuthed) {
    return (
      <div className="panel">
        <h3>Watchlist</h3>
        <p className="muted">Sign in to build a watchlist.</p>
      </div>
    );
  }

  const alreadyWatched = items.some((it) => it.symbol.ticker === activeSymbol);

  return (
    <div className="panel">
      <h3>Watchlist</h3>
      <button className="add-btn" onClick={add} disabled={!activeSymbol || alreadyWatched}>
        {alreadyWatched ? `✓ ${activeSymbol} in watchlist` : `+ Add ${activeSymbol || "symbol"}`}
      </button>
      {error && <p className="error">{error}</p>}
      <ul className="watch-list">
        {items.map((it) => (
          <li key={it.id} className="watch-item">
            <span onClick={() => activePane && selectSymbol(activePane.id, it.symbol.ticker)}>{it.symbol.ticker}</span>
            <button onClick={() => remove(it.id)}>×</button>
          </li>
        ))}
        {items.length === 0 && <li className="muted">Empty</li>}
      </ul>
    </div>
  );
}
