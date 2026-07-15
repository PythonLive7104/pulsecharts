// Signals feed page (Section 13, 19). Follow strategies, see your quota-capped
// personalized feed of generated signals.
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useStore } from "../store/useStore";
import ThemeToggle from "../components/ThemeToggle";
import SignalCard from "../components/SignalCard";
import StrategyBuilder from "../components/StrategyBuilder";
import WeekendNotice from "../components/WeekendNotice";
import { timeAgo, fullTime } from "../lib/time";
import Logo from "../components/Logo";

export default function SignalsPage() {
  const logout = useStore((s) => s.logout);
  const entitlements = useStore((s) => s.entitlements);
  const loadEntitlements = useStore((s) => s.loadEntitlements);

  const [services, setServices] = useState([]);
  const [customQuota, setCustomQuota] = useState(null);
  const [showBuilder, setShowBuilder] = useState(false);
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
  const [loadingMore, setLoadingMore] = useState(false);

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
  // Append the next page of live cards. Only the `signals` array grows — the rest of
  // the payload (quota, resolved history) belongs to page 0 and is left alone.
  async function loadMore() {
    if (loadingMore || !feed?.has_more) return;
    setLoadingMore(true);
    try {
      const next = await api.signalFeed(feed.signals.length);
      setFeed((prev) => ({
        ...prev,
        signals: [...prev.signals, ...(next.signals || [])],
        signals_total: next.signals_total ?? prev.signals_total,
        has_more: next.has_more,
      }));
    } catch {
      /* leave the button in place so the user can retry */
    } finally {
      setLoadingMore(false);
    }
  }

  async function load(silent = false) {
    if (!silent) setLoading(true);

    // Accuracy + Telegram status are decoration: the page is perfectly usable without
    // them and they pop in when they land. Firing them alongside the blocking set (so
    // they still share the same round trip) but NOT awaiting them keeps the whole page
    // from being held hostage by the slowest request — previously a single Promise.all
    // over all five meant a blank "Loading…" until every last one returned.
    // Realized accuracy is a STAFF-ONLY analysis surface (the API 403s for everyone
    // else). Users still see every trade they were sent — outcomes and all — in Trade
    // updates and Past results; only the aggregate stat panel is internal.
    api.signalAccuracy().then(setAccuracy).catch(() => setAccuracy(null));
    api.telegramStatus().then(setTg).catch(() => setTg(null));

    try {
      // The three the page genuinely cannot render without.
      const [svc, sub, fd] = await Promise.all([
        api.signalServices(),
        api.signalSubscriptions(),
        api.signalFeed(),
      ]);
      // /signal-services/ now returns { services, custom_quota }.
      setServices(svc?.services ?? svc ?? []);
      setCustomQuota(svc?.custom_quota ?? null);
      setSubs(sub);
      setFeed(fd);
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
      // e.g. free-tier 403 "Your plan lets you follow 4 strategies. Upgrade…"
      setFollowError(e.message);
    }
  }

  const isPro = entitlements?.plan_key === "pro";

  async function deleteStrategy(svc) {
    if (!window.confirm(`Delete "${svc.name}"? This removes its signals. Your monthly quota is not refunded.`)) return;
    setFollowError(null);
    try {
      await api.deleteStrategy(svc.id);
      await load();
    } catch (e) {
      setFollowError(e.message);
    }
  }

  async function disconnectTelegram() {
    await api.telegramDisconnect();
    await load();
  }

  async function reconnectTelegram() {
    await api.telegramReconnect();
    await load();
  }

  const WIN_OUTCOMES = new Set(["TP1", "TP2", "TP3", "TP4"]);
  const resolved = feed?.resolved || [];
  const winCount = resolved.filter((s) => WIN_OUTCOMES.has(s.outcome)).length;
  const lossCount = resolved.filter((s) => s.outcome === "SL").length;
  // Neither a win nor a loss: the trend flipped (INVALID) or the call ran out of time
  // without touching a stop or a target (EXPIRED). Both close flat. They're in the
  // list, so give them a chip — otherwise the header count exceeds W+L with nothing
  // to explain the gap.
  const isFlat = (s) => s.outcome === "INVALID" || s.outcome === "EXPIRED";
  const flatCount = resolved.filter(isFlat).length;
  // Open trades that already banked a target. The accuracy headline counts these (at
  // their locked floor), so they must be inspectable HERE too — otherwise the two
  // panels quote different populations with nothing on screen to reconcile them.
  const running = (feed?.signals || []).filter((s) => s.best_tp > 0);
  const shownResults = (resultFilter === "running" ? running : resolved).filter((s) =>
    resultFilter === "win"
      ? WIN_OUTCOMES.has(s.outcome)
      : resultFilter === "loss"
        ? s.outcome === "SL"
        : resultFilter === "flat"
          ? isFlat(s)
          : true,
  );

  const quota = entitlements?.signal_weekly_quota;
  const quotaLabel = quota === -1 ? "Unlimited" : quota;
  // Locked only when the server says this plan has no signal access (quota 0).
  // Free/Starter get a (smaller) in-app feed + trade updates; the Telegram panel
  // still pitches the premium upgrade.
  const locked = Boolean(feed?.locked);

  // Plan-aware upsell: Free and Starter have a capped weekly feed (20 / 400), so
  // nudge them to upgrade for more — Pro is unlimited, so it never shows. The
  // copy sharpens once they've used up this week's allowance.
  const planKey = entitlements?.plan_key;
  const showUpsell = planKey === "free" || planKey === "starter";
  const usedThisWeek = feed?.delivered_this_week ?? 0;
  const atCap = quota != null && quota !== -1 && usedThisWeek >= quota;
  const upsell = showUpsell ? (
    <div className="upgrade-banner">
      <div className="ub-text">
        <strong>
          {atCap
            ? "You've reached this week's signal limit"
            : `${entitlements?.plan_label || "Free"} plan · ${usedThisWeek} of ${quota} signals this week`}
        </strong>
        <span className="muted">
          {planKey === "free"
            ? "Upgrade to Starter for 400 signals/week and Telegram alerts — or Pro for unlimited."
            : "Upgrade to Pro for unlimited signals."}
        </span>
      </div>
      <Link to="/account/billing" className="btn-primary">
        {planKey === "free" ? "Upgrade" : "Upgrade to Pro"}
      </Link>
    </div>
  ) : null;

  // Recent trade updates (closures) — shown in-app to everyone, so free/starter
  // who don't use Telegram still see when a trade hit TP/SL or the trend flipped.
  // Scale-out model (§19.2): a partial is banked at each target and the stop trails
  // to breakeven after TP1, so a TP1/TP2 close means the runner came back to
  // breakeven with the earlier third(s) already secured; TP3 is a full run.
  const CLOSURE_MSG = {
    TP1: "✅ TP1 banked · runner to breakeven",
    TP2: "✅ TP1 & TP2 banked · runner to breakeven",
    TP3: "✅ Full run — all targets hit",
    TP4: "✅ Full run — all targets hit",
    SL: "🛑 Stopped out", INVALID: "⚠️ Invalidated — trend flipped",
    EXPIRED: "⌛ Expired",
  };
  // Trade updates = every event Telegram would have pushed you, in one list: trades
  // that CLOSED (feed.resolved — delivered-only server-side) *and* still-open trades
  // that TAGGED a target (feed.signals with best_tp > 0). Leaving the second kind out
  // was why a trade could bank TP1/TP2 on Telegram while the dashboard showed nothing:
  // an open trade never resolves, so it never entered a resolved-only list.
  const RECENT = 48 * 3600 * 1000;
  const fresh = (t) => t && Date.now() - new Date(t).getTime() < RECENT;

  // Open trades that have tagged a target. `best_tp_at` is only stamped when a target
  // NEWLY tags, so trades that banked one before that field existed have none — fall
  // back to entry time (an honest lower bound) so they still appear.
  const progressUpdates = (feed?.signals || [])
    .map((s) => ({ ...s, best_tp_at: s.best_tp_at || s.generated_at }))
    .filter((s) => s.best_tp > 0)
    .map((s) => ({ s, at: s.best_tp_at, cls: "win",
      msg: `🎯 TP${s.best_tp} tagged · running${s.best_tp > 1 ? "" : " · stop to breakeven"}` }));

  const closureUpdates = (feed?.resolved || [])
    .filter((s) => fresh(s.resolved_at))
    .map((s) => ({ s, at: s.resolved_at, msg: CLOSURE_MSG[s.outcome] || s.outcome, cls:
      ["TP1", "TP2", "TP3", "TP4"].includes(s.outcome) ? "win" : s.outcome === "SL" ? "loss" : "neutral" }));

  // One chronological event log, newest first. Progress and closures are NOT
  // segregated — pinning all running trades above all closures made a 21h-old "TP
  // tagged" sit above a 2h-old closure, which reads as broken. The freshness of the
  // event is what orders it; the row's colour/message still says what kind it is.
  const recentClosures = [...progressUpdates, ...closureUpdates]
    .sort((a, b) => new Date(b.at) - new Date(a.at))
    .slice(0, 8);

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
        ) : tg.can_reconnect ? (
          <>
            <div className="tg-text">
              <strong>Telegram disconnected</strong>
              <span className="muted">We still have your chat on file — reconnect to start receiving signals again.</span>
            </div>
            <button className="btn-primary" onClick={reconnectTelegram}>Reconnect</button>
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
                AI-generated buy/sell signals with entry, stop-loss, and TP1–TP3 targets,
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
          {isPro ? (
            <button
              className="sb-create"
              onClick={() => setShowBuilder(true)}
              disabled={(customQuota?.remaining ?? 0) <= 0}
            >
              <span className="sb-create-main">＋ Create your own strategy</span>
              {customQuota && (
                <span className="sb-create-count">{customQuota.remaining}/{customQuota.limit} left</span>
              )}
            </button>
          ) : (
            // Free/Starter still see the feature — clearly flagged as Pro — so they
            // know it exists and can upgrade into it.
            <Link to="/account/billing" className="sb-create sb-create-locked">
              <span className="sb-create-main">
                🔒 Build your own strategy <span className="feature-tag premium">Pro</span>
              </span>
              <span className="sb-create-sub">
                Describe a setup in plain English and our AI turns it into a live
                strategy. Upgrade to Pro to build up to 5 a month →
              </span>
            </Link>
          )}
          {followError && <p className="error">{followError}</p>}
          {services.map((svc) => {
            const followed = Boolean(subByService[svc.id]);
            return (
              <div key={svc.id} className={`strategy ${followed ? "followed" : ""}`}>
                <div className="strategy-info">
                  <strong>
                    {svc.name}
                    {svc.is_custom && <span className="strategy-badge">Custom</span>}
                  </strong>
                  <span className="muted">{svc.is_custom ? svc.rule_summary : svc.description}</span>
                </div>
                <div className="strategy-actions">
                  <button className={followed ? "btn-ghost" : "btn-primary"} onClick={() => toggle(svc)}>
                    {followed ? "Following" : "Follow"}
                  </button>
                  {svc.is_custom && (
                    <button className="strategy-del" aria-label="Delete strategy" onClick={() => deleteStrategy(svc)}>✕</button>
                  )}
                </div>
              </div>
            );
          })}
        </aside>

        {showBuilder && (
          <StrategyBuilder
            quota={customQuota}
            onClose={() => setShowBuilder(false)}
            onCreated={async () => { setShowBuilder(false); await load(); }}
          />
        )}

        <WeekendNotice pause={feed?.pause} />

        <section className="feed">
          <div className="feed-head">
            <h1>Your signal feed</h1>
            {feed && (
              <span className="quota-chip">
                {feed.delivered_this_week} this week{quota != null && ` · ${quotaLabel} / week`}
              </span>
            )}
          </div>

          {upsell}

          {telegramPanel}

          {recentClosures.length > 0 && (
            <div className="trade-updates">
              <h3>Trade updates</h3>
              {recentClosures.map(({ s, at, msg, cls }) => (
                <div key={`${s.id}-${at}`} className={`tu-row ${cls}`}>
                  <span className="tu-sym">{s.symbol} {s.direction} · {s.timeframe}</span>
                  <span className="tu-msg">{msg}</span>
                  <time className="tu-time muted" dateTime={at} title={fullTime(at)}>
                    {timeAgo(at)}
                  </time>
                </div>
              ))}
            </div>
          )}

          {accuracy && (
            <div className="accuracy-panel">
              <div className="acc-head">
                <h3>Your realized accuracy</h3>
                <span className="muted">{accuracy.note}</span>
              </div>
              {accuracy.overall.resolved > 0 ? (
                <div className="acc-body">
                  {/* A dozen trades is noise, not a track record. Say so plainly rather
                      than rendering a small sample as a confident headline. */}
                  {accuracy.provisional && (
                    <p className="acc-provisional muted">
                      Provisional — only {accuracy.overall.resolved} resolved{" "}
                      {accuracy.overall.resolved === 1 ? "trade" : "trades"} so far. A sample
                      this small swings hard on a single result and isn&apos;t a reliable
                      guide to future performance; it settles as it approaches{" "}
                      {accuracy.min_sample}+.
                    </p>
                  )}
                  <div className="acc-bigstat">
                    <span className="acc-rate">{accuracy.overall.win_rate}%</span>
                    <span className="muted">
                      {accuracy.overall.wins}W / {accuracy.overall.losses}L over{" "}
                      {accuracy.overall.resolved} trades
                      {accuracy.overall.breakeven > 0 && ` · ${accuracy.overall.breakeven} invalidated`}
                      {accuracy.overall.avg_r != null && (
                        <> · <b>{accuracy.overall.avg_r > 0 ? "+" : ""}{accuracy.overall.avg_r}R</b> avg / trade</>
                      )}
                    </span>
                    {/* The headline leans on open positions, so the settled record and
                        the undecided pile both stay visible next to it. Without the
                        undecided count the figure would be the open WINNERS only. */}
                    {accuracy.closed_only && (accuracy.overall.running > 0 || accuracy.undecided > 0) && (
                      <span className="muted acc-split">
                        Settled: <b>{accuracy.closed_only.win_rate ?? "—"}%</b>{" "}
                        ({accuracy.closed_only.wins}W / {accuracy.closed_only.losses}L
                        {accuracy.closed_only.avg_r != null && (
                          <>, {accuracy.closed_only.avg_r > 0 ? "+" : ""}
                          {accuracy.closed_only.avg_r}R</>
                        )} closed)
                        {accuracy.overall.running > 0 &&
                          ` · ${accuracy.overall.running} running with TP1 banked (can't lose, counted at floor)`}
                        {accuracy.undecided > 0 &&
                          ` · ${accuracy.undecided} open and undecided — not counted either way`}
                      </span>
                    )}
                  </div>
                  <div className="acc-strats">
                    {accuracy.strategies.filter((s) => s.resolved > 0).map((s) => (
                      <div key={s.slug} className="acc-strat">
                        <span>{s.name}</span>
                        <span className="acc-strat-rate">
                          {s.win_rate}% <span className="muted">({s.wins}/{s.resolved})
                          {s.avg_r != null && `, ${s.avg_r > 0 ? "+" : ""}${s.avg_r}R`}</span>
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
              Your past results ({resolved.length} closed
              {running.length > 0 && ` · ${running.length} running`})
              <span className="results-wl">
                <span className="win">{winCount}W</span> / <span className="loss">{lossCount}L</span>
                {flatCount > 0 && <span className="muted"> / {flatCount} flat</span>}
              </span>
            </button>

            {showResults && (
              resolved.length === 0 ? (
                <p className="muted results-empty">
                  No resolved signals yet. Signals delivered to you show up here —
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
                    {flatCount > 0 && (
                      <button
                        className={`chip ${resultFilter === "flat" ? "active" : ""}`}
                        onClick={() => setResultFilter("flat")}
                        title="Closed flat — the trend flipped, or the call ran out of time without hitting a stop or a target. Neither a win nor a loss, so excluded from the win rate."
                      >
                        — Flat ({flatCount})
                      </button>
                    )}
                    {running.length > 0 && (
                      <button
                        className={`chip ${resultFilter === "running" ? "active" : ""}`}
                        onClick={() => setResultFilter("running")}
                        title="Still open, but already banked TP1+ with the stop at breakeven — they can no longer become losses. The accuracy figure counts these at their locked-in floor, which is why it reads higher than the closed-only record."
                      >
                        ● Running ({running.length})
                      </button>
                    )}
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
            {feed?.has_more && (
              <button className="load-more" onClick={loadMore} disabled={loadingMore}>
                {loadingMore
                  ? "Loading…"
                  : `Show more (${feed.signals.length} of ${feed.signals_total})`}
              </button>
            )}
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
