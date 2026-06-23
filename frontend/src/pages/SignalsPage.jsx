// Signals feed page (Section 13, 19). Follow strategies, see your quota-capped
// personalized feed of generated signals.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useStore } from "../store/useStore";
import ThemeToggle from "../components/ThemeToggle";
import SignalCard from "../components/SignalCard";
import Logo from "../components/Logo";

export default function SignalsPage() {
  const logout = useStore((s) => s.logout);
  const entitlements = useStore((s) => s.entitlements);
  const loadEntitlements = useStore((s) => s.loadEntitlements);

  const [services, setServices] = useState([]);
  const [subs, setSubs] = useState([]);
  const [feed, setFeed] = useState(null);
  const [accuracy, setAccuracy] = useState(null);
  const [loading, setLoading] = useState(true);

  // Past-results panel: toggled open by a button, filterable by win/loss.
  const [showResults, setShowResults] = useState(false);
  const [resultFilter, setResultFilter] = useState("all");
  // Surfaced when following a strategy is rejected (e.g. free-tier limit).
  const [followError, setFollowError] = useState(null);
  // Telegram delivery (premium): connection status + deep link.
  const [tg, setTg] = useState(null);

  // Match the signal feed's scroll height to the strategies sidebar so the feed
  // scrolls internally instead of running the page on forever.
  const stratRef = useRef(null);
  const [scrollH, setScrollH] = useState(null);
  useEffect(() => {
    const el = stratRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([entry]) => setScrollH(entry.contentRect.height));
    ro.observe(el);
    return () => ro.disconnect();
  }, [feed]);

  // `silent` skips the loading spinner — used by the background poll so the feed
  // refreshes in place without flickering.
  async function load(silent = false) {
    if (!silent) setLoading(true);
    try {
      const [svc, sub, fd, acc, tgStatus] = await Promise.all([
        api.signalServices(),
        api.signalSubscriptions(),
        api.signalFeed(),
        api.signalAccuracy().catch(() => null),
        api.telegramStatus().catch(() => null),
      ]);
      setServices(svc);
      setSubs(sub);
      setFeed(fd);
      setAccuracy(acc);
      setTg(tgStatus);
    } catch {
      /* not authed / offline */
    } finally {
      if (!silent) setLoading(false);
    }
  }

  useEffect(() => {
    load();
    loadEntitlements();
    // New signals are generated every scan interval, so poll the feed in the
    // background and re-render when fresh ones land — no manual refresh needed.
    const id = setInterval(() => load(true), 30000);
    return () => clearInterval(id);
  }, [loadEntitlements]);

  const subByService = Object.fromEntries(subs.map((s) => [s.service.id, s.id]));

  async function toggle(svc) {
    const subId = subByService[svc.id];
    setFollowError(null);
    try {
      if (subId) await api.unfollowService(subId);
      else await api.followService(svc.id);
      await load();
    } catch (e) {
      // e.g. free-tier 403 "Your plan lets you follow 1 strategy. Upgrade…"
      setFollowError(e.message);
    }
  }

  async function disconnectTelegram() {
    await api.telegramDisconnect();
    await load();
  }

  const WIN_OUTCOMES = new Set(["TP1", "TP2", "TP3", "TP4"]);
  const resolved = feed?.resolved || [];
  const winCount = resolved.filter((s) => WIN_OUTCOMES.has(s.outcome)).length;
  const lossCount = resolved.filter((s) => s.outcome === "SL").length;
  const shownResults = resolved.filter((s) =>
    resultFilter === "win"
      ? WIN_OUTCOMES.has(s.outcome)
      : resultFilter === "loss"
        ? s.outcome === "SL"
        : true,
  );

  const quota = entitlements?.signal_daily_quota;
  const quotaLabel = quota === -1 ? "Unlimited" : quota;
  // Locked only when the server says this plan has no signal access (quota 0).
  // Free/Starter get a (smaller) in-app feed + trade updates; the Telegram panel
  // still pitches the premium upgrade.
  const locked = Boolean(feed?.locked);

  // Recent trade updates (closures) — shown in-app to everyone, so free/starter
  // who don't use Telegram still see when a trade hit TP/SL or the trend flipped.
  const CLOSURE_MSG = {
    TP1: "✅ Hit TP1", TP2: "✅ Hit TP2", TP3: "✅ Hit TP3", TP4: "✅ Hit TP4",
    SL: "🛑 Stopped out", INVALID: "⚠️ Trend changed — consider closing",
    EXPIRED: "⌛ Expired",
  };
  const recentClosures = (feed?.resolved || [])
    .filter((s) => s.resolved_at && Date.now() - new Date(s.resolved_at).getTime() < 48 * 3600 * 1000)
    .slice(0, 6);

  // Telegram delivery panel — shown to ALL users. Premium users can connect;
  // everyone else sees an upgrade prompt. Rendered in both the locked and feed
  // views so it's always visible once the bot is configured server-side.
  const telegramPanel = tg?.configured ? (
    <div className="telegram-panel">
      <span className="tg-icon">✈️</span>
      {tg.is_premium ? (
        tg.connected ? (
          <>
            <div className="tg-text">
              <strong>Telegram connected</strong>
              <span className="muted">New signals from your followed strategies are sent to your Telegram.</span>
            </div>
            <button className="btn-ghost" onClick={disconnectTelegram}>Disconnect</button>
          </>
        ) : (
          <>
            <div className="tg-text">
              <strong>Get signals on Telegram</strong>
              <span className="muted">
                Tap the button — Telegram opens our bot, you press <b>Start</b>, and you're linked.
                No username or setup to type. Signals then arrive instantly.
              </span>
            </div>
            {tg.link_url && (
              <a className="btn-primary" href={tg.link_url} target="_blank" rel="noreferrer">Connect Telegram</a>
            )}
          </>
        )
      ) : (
        <>
          <div className="tg-text">
            <strong>Signals on Telegram <span className="feature-tag premium">Starter &amp; Pro</span></strong>
            <span className="muted">Get every signal pushed straight to your Telegram — included on Starter &amp; Pro plans.</span>
          </div>
          <Link className="btn-primary" to="/account/billing">Upgrade</Link>
        </>
      )}
    </div>
  ) : null;

  return (
    <div className="signals-page">
      <header className="topbar">
        <Link to="/" className="brand"><Logo /></Link>
        <nav className="signals-nav">
          <Link to="/app" className="btn-ghost">Charts</Link>
          <span className="active-tab">Signals</span>
          <Link to="/auto-trade" className="btn-ghost">
            Auto-Trade <span className="feature-tag premium nav-pro-tag">Pro</span>
          </Link>
        </nav>
        <div className="topbar-right">
          <ThemeToggle />
          <Link to="/account" className="plan-pill plan-pill-link">
            {entitlements?.is_premium ? "★ " : ""}{entitlements?.plan_label || "Free"} · Account
          </Link>
          <button className="btn-ghost" onClick={logout}>Sign out</button>
        </div>
      </header>

      <main className="signals-body">
        {locked ? (
          <div className="signals-locked">
            <div className="lock-card">
              <div className="lock-icon">🔒</div>
              <h1>Trading Signals</h1>
              <p className="muted">
                AI-generated buy/sell signals with entry, stop-loss, and TP1–TP4 targets,
                confidence scores, and reasoning are included on the Starter &amp; Pro plans.
              </p>
              <Link to="/account/billing" className="btn-primary btn-lg">Upgrade</Link>
            </div>
            {telegramPanel}
          </div>
        ) : (
        <>
        <aside className="strategies" ref={stratRef}>
          <h3>Strategies</h3>
          <p className="muted">Follow a strategy to receive its signals.</p>
          {followError && <p className="error">{followError}</p>}
          {services.map((svc) => {
            const followed = Boolean(subByService[svc.id]);
            return (
              <div key={svc.id} className={`strategy ${followed ? "followed" : ""}`}>
                <div className="strategy-info">
                  <strong>{svc.name}</strong>
                  <span className="muted">{svc.description}</span>
                </div>
                <button className={followed ? "btn-ghost" : "btn-primary"} onClick={() => toggle(svc)}>
                  {followed ? "Following" : "Follow"}
                </button>
              </div>
            );
          })}
        </aside>

        <section className="feed">
          <div className="feed-head">
            <h1>Your signal feed</h1>
            {feed && (
              <span className="quota-chip">
                {feed.delivered_today} today{quota != null && ` · ${quotaLabel} / day`}
              </span>
            )}
          </div>

          {telegramPanel}

          {recentClosures.length > 0 && (
            <div className="trade-updates">
              <h3>Trade updates</h3>
              {recentClosures.map((s) => {
                const win = ["TP1", "TP2", "TP3", "TP4"].includes(s.outcome);
                const cls = win ? "win" : s.outcome === "SL" ? "loss" : "neutral";
                return (
                  <div key={s.id} className={`tu-row ${cls}`}>
                    <span className="tu-sym">{s.symbol} {s.direction} · {s.timeframe}</span>
                    <span className="tu-msg">{CLOSURE_MSG[s.outcome] || s.outcome}</span>
                    <span className="tu-time muted">{new Date(s.resolved_at).toLocaleString()}</span>
                  </div>
                );
              })}
            </div>
          )}

          {accuracy && (
            <div className="accuracy-panel">
              <div className="acc-head">
                <h3>Realized accuracy</h3>
                <span className="muted">{accuracy.note}</span>
              </div>
              {accuracy.overall.resolved > 0 ? (
                <div className="acc-body">
                  <div className="acc-bigstat">
                    <span className="acc-rate">{accuracy.overall.win_rate}%</span>
                    <span className="muted">{accuracy.overall.wins}W / {accuracy.overall.losses}L ({accuracy.overall.resolved} resolved)</span>
                  </div>
                  <div className="acc-strats">
                    {accuracy.strategies.filter((s) => s.resolved > 0).map((s) => (
                      <div key={s.slug} className="acc-strat">
                        <span>{s.name}</span>
                        <span className="acc-strat-rate">
                          {s.win_rate}% <span className="muted">({s.wins}/{s.resolved})</span>
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="muted">No resolved signals yet — accuracy fills in as signals hit their targets or stops.</p>
              )}
            </div>
          )}

          <section className="results-section">
            <button
              className="results-toggle"
              onClick={() => setShowResults((v) => !v)}
              aria-expanded={showResults}
            >
              <span className="results-caret">{showResults ? "▾" : "▸"}</span>
              Past results ({resolved.length})
              <span className="results-wl">
                <span className="win">{winCount}W</span> / <span className="loss">{lossCount}L</span>
              </span>
            </button>

            {showResults && (
              resolved.length === 0 ? (
                <p className="muted results-empty">
                  No resolved signals yet. Calls from the strategies you follow show up here —
                  win or loss — once they hit a take-profit or stop.
                </p>
              ) : (
                <>
                  <div className="result-filters">
                    <button
                      className={`chip ${resultFilter === "all" ? "active" : ""}`}
                      onClick={() => setResultFilter("all")}
                    >
                      All ({resolved.length})
                    </button>
                    <button
                      className={`chip ${resultFilter === "win" ? "active" : ""}`}
                      onClick={() => setResultFilter("win")}
                    >
                      ✓ Wins ({winCount})
                    </button>
                    <button
                      className={`chip ${resultFilter === "loss" ? "active" : ""}`}
                      onClick={() => setResultFilter("loss")}
                    >
                      ✕ Losses ({lossCount})
                    </button>
                  </div>
                  <div className="signal-list signal-scroll">
                    {shownResults.map((s) => <SignalCard key={s.id} s={s} />)}
                    {shownResults.length === 0 && (
                      <p className="muted">No {resultFilter} signals in this window.</p>
                    )}
                  </div>
                </>
              )
            )}
          </section>

          {loading && <p className="muted">Loading…</p>}
          {!loading && feed?.needs_watchlist && (
            <div className="empty-feed">
              <p className="muted">No coins in your watchlist yet.</p>
              <p className="muted">
                Signals are generated for the coins you watch. Add up to{" "}
                {feed.watchlist_limit === -1 ? "unlimited" : feed.watchlist_limit}{" "}
                symbol{feed.watchlist_limit === 1 ? "" : "s"} (your plan’s limit) to start
                receiving signals.
              </p>
              <Link to="/app" className="btn-primary">Add symbols on the chart →</Link>
            </div>
          )}
          {!loading && !feed?.needs_watchlist && feed?.signals?.length === 0 && (
            <div className="empty-feed">
              <p className="muted">No signals yet.</p>
              <p className="muted">
                {subs.length === 0
                  ? "Follow a strategy on the left to start receiving signals."
                  : "Signals appear here as the engine generates them for the coins you watch."}
              </p>
            </div>
          )}
          <div
            className="signal-list signal-scroll"
            style={scrollH ? { height: scrollH, maxHeight: scrollH } : undefined}
          >
            {feed?.signals?.map((s) => <SignalCard key={s.id} s={s} />)}
          </div>

          <p className="feed-disclaimer">
            Signals are algorithmic, informational output — not financial advice. Do your own research.
          </p>
        </section>
        </>
        )}
      </main>
    </div>
  );
}
