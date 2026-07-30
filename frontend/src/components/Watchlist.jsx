// Watchlist panel (Section 5, 12 — tier-capped server-side).
// The list itself lives in the store so this panel and the symbol picker
// (SymbolSearch, which can add/remove inline) always agree.
import { useEffect } from "react";
import { useStore } from "../store/useStore";

export default function Watchlist() {
  const isAuthed = useStore((s) => s.isAuthed);
  const activePane = useStore((s) => s.activePane());
  const activeSymbol = activePane?.symbol || null;
  const selectSymbol = useStore((s) => s.selectSymbol);

  const items = useStore((s) => s.watchlist);
  const error = useStore((s) => s.watchError);
  const loadWatchlist = useStore((s) => s.loadWatchlist);
  const addToWatchlist = useStore((s) => s.addToWatchlist);
  const removeFromWatchlist = useStore((s) => s.removeFromWatchlist);

  useEffect(() => {
    loadWatchlist();
  }, [isAuthed, loadWatchlist]);

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
      <button
        className="add-btn"
        onClick={() => activeSymbol && addToWatchlist(activeSymbol)}
        disabled={!activeSymbol || alreadyWatched}
      >
        {alreadyWatched ? `✓ ${activeSymbol} in watchlist` : `+ Add ${activeSymbol || "symbol"}`}
      </button>
      {error && <p className="error">{error}</p>}
      <ul className="watch-list">
        {items.map((it) => (
          <li key={it.id} className="watch-item">
            <span onClick={() => activePane && selectSymbol(activePane.id, it.symbol.ticker)}>{it.symbol.ticker}</span>
            <button onClick={() => removeFromWatchlist(it.symbol.ticker)}>×</button>
          </li>
        ))}
        {items.length === 0 && <li className="muted">Empty</li>}
      </ul>
    </div>
  );
}
