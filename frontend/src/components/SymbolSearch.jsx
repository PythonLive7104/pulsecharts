// Type-to-filter symbol picker (Section 9 — SymbolSearch). Replaces the plain
// dropdown, which doesn't scale to the full Hyperliquid perp universe (~180).
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useStore } from "../store/useStore";
import { planAllows } from "../lib/plans";

// High enough to render the whole roster: every symbol of the selected asset class
// is listed, in the app's curated order, watchlisted or not. Ordering deliberately
// does NOT float watchlisted symbols to the top — reordering (or capping) the list
// meant symbols moved or vanished as you added/removed them. The panel scrolls and
// the ✓ Added chip marks membership, so nothing is hidden and nothing jumps.
const MAX_RESULTS = 400;

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
  const listRef = useRef(null);

  const watchedTickers = useMemo(
    () => new Set(watchlist.map((it) => it.symbol.ticker)),
    [watchlist]
  );

  const rows = useMemo(() => {
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
    return list.slice(0, MAX_RESULTS);
  }, [symbols, query, assetClass]);

  const watchedShown = rows.filter((s) => watchedTickers.has(s.ticker)).length;
  // Derived from the values this component already subscribes to, so it re-renders
  // the moment the watchlist or the plan changes.
  const watchLimit = entitlements?.watchlist_limit ?? -1;
  const full = watchLimit !== -1 && watchlist.length >= watchLimit;

  // Close on outside click.
  useEffect(() => {
    function onClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  // Keep the highlighted row in view — the list is long enough now that arrow-key
  // navigation would otherwise walk off the bottom of the panel invisibly.
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector(`[data-idx="${highlight}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [highlight, open]);

  function openList() {
    setOpen(true);
    clearWatchError();
    // Start on the symbol currently charted, so a long list opens where you are.
    const i = rows.findIndex((s) => s.ticker === activeSymbol);
    setHighlight(i >= 0 ? i : 0);
  }

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
  function onToggleWatch(e, s) {
    e.preventDefault();
    e.stopPropagation();
    toggleWatchlist(s.ticker);
  }

  function onKeyDown(e) {
    if (!open && (e.key === "ArrowDown" || e.key === "Enter")) {
      openList();
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
        onFocus={openList}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
          setHighlight(0);
        }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <ul className="symbol-results" ref={listRef}>
          {rows.length === 0 && <li className="muted no-match">No matches</li>}
          {(rows.length > 0 || watchError) && (
            // Sticky: the limit warning and any error have to stay on screen while
            // you scroll, or you click Add 40 rows down and never see why nothing
            // happened — which is exactly how this read as broken.
            <li className="symbol-head">
              <div className="symbol-group">
                {rows.length} {assetClass === "forex" ? "pairs" : "coins"} · {watchedShown} in
                your watchlist
              </div>
              {full && !watchError && (
                <div className="symbol-limit">
                  Watchlist full — {watchLimit} of {watchLimit}. Remove one below, or{" "}
                  <Link to="/account/billing">upgrade</Link> for more.
                </div>
              )}
              {watchError && <div className="symbol-watch-error">{watchError}</div>}
            </li>
          )}
          {rows.map((s, i) => {
            const locked = !planAllows(planKey, s.min_plan);
            const watched = watchedTickers.has(s.ticker);
            return (
              <li key={s.id} className="symbol-result-wrap">
                <div
                  data-idx={i}
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
                      // `atLimit` greys the button but keeps it clickable — clicking
                      // is what surfaces the "watchlist full" message, and a silently
                      // dead button is what made this feel broken in the first place.
                      className={`result-add ${watched ? "on" : ""} ${
                        full && !watched ? "atLimit" : ""
                      }`}
                      title={
                        watched
                          ? "Remove from watchlist"
                          : full
                            ? `Watchlist full (${watchLimit} of ${watchLimit}) — remove one or upgrade`
                            : "Add to watchlist"
                      }
                      aria-label={watched ? "Remove from watchlist" : "Add to watchlist"}
                      aria-pressed={watched}
                      onMouseDown={(e) => onToggleWatch(e, s)}
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
        </ul>
      )}
    </div>
  );
}
