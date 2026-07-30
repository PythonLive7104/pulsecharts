// Type-to-filter symbol picker (Section 9 — SymbolSearch). Replaces the plain
// dropdown, which doesn't scale to the full Hyperliquid perp universe (~180).
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useStore } from "../store/useStore";
import { planAllows } from "../lib/plans";

const MAX_RESULTS = 50; // cap the rendered list for snappy filtering
// Rows the watched block gets while there is anything left to add. Kept small enough
// that the "Not added yet" section is reachable without a long scroll: an account
// watching most of the roster (Pro seeds every symbol) would otherwise fill all
// MAX_RESULTS rows with "✓ Added" and have nothing to ADD.
const WATCHED_SLOTS = 8;

export default function SymbolSearch() {
  const symbols = useStore((s) => s.symbols);
  const assetClass = useStore((s) => s.assetClass);
  const activePane = useStore((s) => s.activePane());
  const selectSymbol = useStore((s) => s.selectSymbol);
  const entitlements = useStore((s) => s.entitlements);
  const isAuthed = useStore((s) => s.isAuthed);
  const watchlist = useStore((s) => s.watchlist);
  const toggleWatchlist = useStore((s) => s.toggleWatchlist);
  const watchError = useStore((s) => s.watchError);
  const clearWatchError = useStore((s) => s.clearWatchError);
  const navigate = useNavigate();
  const planKey = entitlements?.plan_key || "free";
  const activeSymbol = activePane?.symbol || null;

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const boxRef = useRef(null);

  const watchedTickers = useMemo(
    () => new Set(watchlist.map((it) => it.symbol.ticker)),
    [watchlist]
  );

  const results = useMemo(() => {
    const q = query.trim().toUpperCase();
    // Scope to the selected asset class (Crypto/Forex), then text-filter.
    const scoped = symbols.filter((s) => (s.asset_class || "crypto") === assetClass);
    const list = q
      ? scoped.filter(
          (s) =>
            s.ticker.toUpperCase().includes(q) ||
            (s.display_name || "").toUpperCase().includes(q)
        )
      : scoped;
    // Watchlisted symbols float to the top — the picker doubles as "the symbols I
    // actually trade". But the watched block only gets the WHOLE list when there is
    // nothing left to add; otherwise it yields slots so the not-yet-added symbols
    // are always reachable (that's the whole point of the Add button).
    const watched = list.filter((s) => watchedTickers.has(s.ticker));
    const rest = list.filter((s) => !watchedTickers.has(s.ticker));
    // Only when there is nothing to add does the watched block take the whole list.
    const watchedSlots = rest.length > 0 ? WATCHED_SLOTS : MAX_RESULTS;
    const shownWatched = watched.slice(0, watchedSlots);
    const shownRest = rest.slice(0, Math.max(MAX_RESULTS - shownWatched.length, 0));
    return {
      rows: [...shownWatched, ...shownRest],
      watchedShown: shownWatched.length,
      watchedTotal: watched.length,
      restTotal: rest.length,
    };
  }, [symbols, query, assetClass, watchedTickers]);

  const rows = results.rows;
  const watchedCount = results.watchedShown;

  // Close on outside click.
  useEffect(() => {
    function onClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  function choose(sym) {
    // `sym` may be a ticker string (keyboard path) or the symbol object.
    const s = typeof sym === "string" ? symbols.find((x) => x.ticker === sym) : sym;
    if (!s) return;
    // Plan-gated symbol the user can't access → send them to upgrade instead of
    // loading a chart the backend would refuse anyway.
    if (!planAllows(planKey, s.min_plan)) {
      setOpen(false);
      navigate("/account/billing");
      return;
    }
    if (activePane) selectSymbol(activePane.id, s.ticker);
    setQuery("");
    setOpen(false);
  }

  // Add/Remove toggle: adds/removes without selecting the symbol or closing the
  // list, so a user can curate their whole watchlist in one pass.
  function onStar(e, s) {
    e.preventDefault();
    e.stopPropagation();
    toggleWatchlist(s.ticker);
  }

  function onKeyDown(e) {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      setOpen(true);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlight((h) => Math.min(h + 1, rows.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (rows[highlight]) choose(rows[highlight].ticker);
    } else if (e.key === " " && e.ctrlKey) {
      // Ctrl+Space toggles the highlighted row's watchlist membership.
      e.preventDefault();
      if (rows[highlight]) toggleWatchlist(rows[highlight].ticker);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="symbol-search" ref={boxRef}>
      <input
        className="symbol-search-input"
        value={open ? query : ""}
        placeholder={activeSymbol || "Search symbol…"}
        onFocus={() => {
          setOpen(true);
          setHighlight(0);
          clearWatchError();
        }}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setHighlight(0);
        }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul className="symbol-results">
          {watchError && <li className="symbol-watch-error">{watchError}</li>}
          {rows.length === 0 && <li className="muted no-match">No matches</li>}
          {rows.map((s, i) => {
            const locked = !planAllows(planKey, s.min_plan);
            const watched = watchedTickers.has(s.ticker);
            // Header rows split the watchlisted block from everything else. The
            // first says how much of the watchlist is shown, so a truncated block
            // reads as "type to find the rest", not "that's all of it".
            const header =
              watchedCount > 0 && i === 0
                ? results.watchedTotal > watchedCount
                  ? `In your watchlist · ${watchedCount} of ${results.watchedTotal} — type to find others`
                  : "In your watchlist"
                : watchedCount > 0 && i === watchedCount
                ? "Not added yet"
                : null;
            return (
              <li key={s.id} className="symbol-result-wrap">
                {header && <div className="symbol-group">{header}</div>}
                <div
                  className={`symbol-result ${i === highlight ? "highlight" : ""} ${
                    s.ticker === activeSymbol ? "current" : ""
                  } ${locked ? "locked" : ""} ${watched ? "watched" : ""}`}
                  onMouseEnter={() => setHighlight(i)}
                  onMouseDown={(e) => {
                    e.preventDefault(); // keep focus so onClick fires before blur
                    choose(s);
                  }}
                >
                  <span className="result-ticker">{s.ticker}</span>
                  <span className="result-name">{s.display_name}</span>
                  {locked && (
                    <span className="result-lock" title="Upgrade to access">
                      🔒 {(s.min_plan || "pro").toUpperCase()}
                    </span>
                  )}
                  {isAuthed && !locked && (
                    <button
                      type="button"
                      className={`result-add ${watched ? "on" : ""}`}
                      title={watched ? "Remove from watchlist" : "Add to watchlist"}
                      aria-label={watched ? "Remove from watchlist" : "Add to watchlist"}
                      aria-pressed={watched}
                      onMouseDown={(e) => onStar(e, s)}
                      onClick={(e) => e.stopPropagation()}
                    >
                      {/* Watched rows read "✓ Added", and flip to "Remove" on hover
                          so the click's effect is never a guess. */}
                      <span className="add-label">{watched ? "✓ Added" : "+ Add"}</span>
                      {watched && <span className="add-label-hover">Remove</span>}
                    </button>
                  )}
                </div>
              </li>
            );
          })}
          {/* Nothing left to add is a state worth stating — otherwise a list of all
              "✓ Added" rows looks like the picker is broken. */}
          {rows.length > 0 && results.restTotal === 0 && (
            <li className="symbol-foot muted">
              Every {assetClass === "forex" ? "pair" : "coin"} we track is already in your
              watchlist.
            </li>
          )}
        </ul>
      )}
    </div>
  );
}
