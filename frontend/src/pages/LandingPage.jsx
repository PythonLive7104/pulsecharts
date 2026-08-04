// Marketing landing page (Section 1, 5, 12). Positions PulseCharts as the
// affordable, crypto-focused charting tool. Crypto-only by design (Section 5).
// Honest copy: no accuracy/return claims (signals are v2 and out of scope here).
import { lazy, Suspense, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import ThemeToggle from "../components/ThemeToggle";
import Logo from "../components/Logo";
import SupportChat from "../components/SupportChat";
import { useStore } from "../store/useStore";
import { api } from "../api";
import { LIFETIME_FALLBACK, PLAN_FALLBACK, isLifetime } from "../lib/plans";
import LifetimePrice from "../components/LifetimePrice";

// Lazy so three.js + R3F load in their own chunk only on the landing page,
// keeping the trading app bundle lean.
const ThreeScene = lazy(() => import("../components/hero3d/ThreeScene"));

const FREE_INDICATORS = ["SMA", "EMA", "Volume"];
const PREMIUM_INDICATORS = [
  "RSI", "MACD", "Bollinger Bands", "Stochastic",
  "ATR", "Fibonacci", "VWAP", "Ichimoku Cloud",
];

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

// Rotated through the headline. Visitors told us the page never said WHAT the
// product is; naming both markets in the first line fixes that before they read
// a word of the subhead.
const HERO_MARKETS = ["crypto", "forex"];

// Sample card shown beside the hero copy. Deliberately a STATIC illustration with
// round numbers, not a real signal: the point is to show the shape of what you get
// (entry, stop, three targets, the reason) without implying a live call or a result.
const HERO_SAMPLE = {
  symbol: "BTC-USD", timeframe: "4h", conviction: 84, agree: 4,
  entry: "64,120", stop: "62,515", risk: "2.5",
  tps: [["TP1", "65,725", "+2.5%", "1R"], ["TP2", "67,330", "+5.0%", "2R"], ["TP3", "68,935", "+7.5%", "3R"]],
  why: "EMA9 above EMA21, MACD histogram expanding, RSI 61 — four strategies agree.",
};

const FAQS = [
  {
    q: "Where does the price data come from?",
    a: "Live market data from Hyperliquid's public WebSocket feed, relayed through our servers to your browser so you get a single low-latency stream.",
  },
  {
    q: "Is it really free?",
    a: "Yes. Live charts, every timeframe, and the SMA/EMA/Volume indicators are free forever — no card required. Premium unlocks advanced indicators and saved layouts.",
  },
  {
    q: "Do you support forex, or only crypto?",
    a: "Both. We chart the perps and spot pairs listed on Hyperliquid, plus 21 forex pairs and gold (XAU/USD). The free tier covers 11 pairs — the 7 majors and popular crosses — and Pro unlocks the full set plus gold. Switch between crypto and forex with one toggle; the same indicators and trading signals work across both.",
  },
  {
    q: "Can I build my own trading strategy?",
    a: "Yes — on the Pro plan. Just describe your idea in a sentence (e.g. \"buy when RSI is below 30 and price is above the 200 EMA\") and our AI turns it into a live strategy using real indicators — no coding or formulas. It shows you what it built so you can confirm it, then generates signals for the coins on your watchlist, in-app and on Telegram. You can create up to 5 strategies a month. Custom strategies aren't backtested and are informational only, not financial advice.",
  },
  {
    q: "Is this financial advice?",
    a: "No. PulseCharts is a charting and analysis tool. Nothing here is a recommendation to buy or sell. Always do your own research.",
  },
];

export default function LandingPage() {
  // Headline word rotator (crypto <-> forex).
  const [marketIdx, setMarketIdx] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setMarketIdx((i) => (i + 1) % HERO_MARKETS.length), 2600);
    return () => clearInterval(t);
  }, []);

  // Live market count for the status pill. Real numbers rather than a hardcoded
  // claim that goes stale every time sync_symbols runs — and silently omitted if
  // the request fails, so the hero can never show a made-up figure.
  const [markets, setMarkets] = useState(null);
  useEffect(() => {
    let alive = true;
    api.symbols()
      .then((list) => {
        if (!alive || !Array.isArray(list)) return;
        setMarkets({
          crypto: list.filter((s) => (s.asset_class || "crypto") === "crypto").length,
          forex: list.filter((s) => s.asset_class === "forex").length,
        });
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  const isAuthed = useStore((s) => s.isAuthed);
  const entitlements = useStore((s) => s.entitlements);
  const loadEntitlements = useStore((s) => s.loadEntitlements);
  const logout = useStore((s) => s.logout);
  const [plans, setPlans] = useState(PLAN_FALLBACK);
  const [lifetime, setLifetime] = useState(LIFETIME_FALLBACK);
  // "monthly" | "lifetime" — which billing period the pricing grid shows.
  const [billing, setBilling] = useState("monthly");

  useEffect(() => {
    api.plans()
      .then((d) => {
        if (d?.plans?.length) setPlans(d.plans);
        if (d?.lifetime) setLifetime(d.lifetime);
      })
      .catch(() => { /* keep fallback */ });
  }, []);

  // Needed only to know whether this visitor already owns lifetime, in which case
  // the whole pricing section (and its nav link) is hidden.
  useEffect(() => {
    if (isAuthed) loadEntitlements();
  }, [isAuthed, loadEntitlements]);

  const ownsLifetime = isLifetime(entitlements);
  // Break-even framing for the lifetime card, derived from live prices so the copy
  // can't drift if either price changes.
  const monthlyPro = plans.find((p) => p.key === "pro")?.price_usd || 0;
  const breakEvenMonths = monthlyPro > 0 ? Math.ceil(lifetime.price_usd / monthlyPro) : null;

  return (
    <>
      <header className="landing-nav">
        <div className="landing-nav-inner">
          <span className="brand"><Logo /></span>
          <nav className="landing-nav-links">
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href="#indicators">Indicators</a>
          {!ownsLifetime && <a href="#pricing">Pricing</a>}
          <a href="#faq">FAQ</a>
          <ThemeToggle />
          {isAuthed ? (
            <Link to="/app" className="btn-primary">Open app →</Link>
          ) : (
            <>
              <Link to="/login" className="btn-ghost">Sign in</Link>
              <Link to="/signup" className="btn-primary">Get started</Link>
            </>
          )}
        </nav>
        </div>
      </header>

      <div className="landing">
      <main>
      {/* Hero — real-3D stage: spinning coin + particle field + wireframe terrain */}
      <section className="hero-motion">
        <Suspense fallback={null}>
          <ThreeScene />
        </Suspense>
        <div className="hero-vignette" aria-hidden="true" />

        <div className="hero-inner">
          <div className="hero-copy">
            {/* Status pill: a live, verifiable number instead of an adjective. */}
            <div className="hero-pill hero-anim">
              <span className="hero-pill-dot" aria-hidden="true" />
              LIVE
              {markets
                ? ` · ${markets.crypto} COINS + ${markets.forex} FX PAIRS`
                : " · CRYPTO & FOREX"}
            </div>
            <h1 className="hero-anim">
              Trade{" "}
              <span className="hero-rotate">
                <span key={HERO_MARKETS[marketIdx]} className="grad">
                  {HERO_MARKETS[marketIdx]}
                </span>
              </span>{" "}
              with a plan,<br />not a hunch.
            </h1>
            <p className="hero-sub hero-anim">
              Live candlestick charts and 11 indicators, plus an always-on engine that
              posts <strong>entry, stop-loss and three take-profit targets</strong> with
              the reason behind every call — in the app or straight to your Telegram.
            </p>
            <div className="hero-cta hero-anim">
              {isAuthed ? (
                <Link to="/app" className="btn-primary btn-lg">Open dashboard →</Link>
              ) : (
                <>
                  <Link to="/signup" className="btn-primary btn-lg">Start free →</Link>
                  <a href="#how" className="btn-ghost btn-lg">How it works</a>
                  <a href="#pricing" className="btn-ghost btn-lg">Pricing</a>
                </>
              )}
            </div>
            <p className="hero-note hero-anim">
              No card required · Crypto &amp; Forex · Signals free for 30 days
            </p>
          </div>

          {/* Show the product, don't just describe it — this is the single thing
              visitors said was missing: what do I actually get? */}
          <div className="hero-demo hero-anim" id="signals-sample" aria-label="Example signal">
            <div className="hd-head">
              <span className="hd-dir">🟢 BUY {HERO_SAMPLE.symbol}</span>
              <span className="hd-tf">{HERO_SAMPLE.timeframe}</span>
            </div>
            <div className="hd-badges">
              {HERO_SAMPLE.conviction}% conviction · {HERO_SAMPLE.agree} strategies agree
            </div>
            <dl className="hd-levels">
              <div><dt>Entry</dt><dd>{HERO_SAMPLE.entry}</dd><dd className="hd-meta" /></div>
              <div className="hd-stop">
                <dt>Stop</dt><dd>{HERO_SAMPLE.stop}</dd>
                <dd className="hd-meta">risk {HERO_SAMPLE.risk}%</dd>
              </div>
              {HERO_SAMPLE.tps.map(([label, price, pct, r]) => (
                <div key={label} className="hd-tp">
                  <dt>{label}</dt><dd>{price}</dd>
                  <dd className="hd-meta">{pct} ({r})</dd>
                </div>
              ))}
            </dl>
            <p className="hd-why">{HERO_SAMPLE.why}</p>
            <p className="hd-foot">Example only — not a live signal or financial advice.</p>
          </div>
        </div>

        <div className="hero-scroll" aria-hidden="true">
          <span className="hero-scroll-pill"><span className="hero-scroll-dot" /></span>
          <span className="hero-scroll-text">Scroll to discover</span>
        </div>
      </section>

      {/* Trust band */}
      <section className="trust-band">
        <div><strong>Real-time</strong><span>WebSocket feed</span></div>
        <div><strong>{TIMEFRAMES.length}+</strong><span>timeframes</span></div>
        <div><strong>11</strong><span>indicators</span></div>
        <div><strong>$0</strong><span>to start</span></div>
      </section>

      {/* How it works */}
      <section id="how" className="how">
        <h2>How to use PulseCharts</h2>
        <p className="section-sub">
          From sign-up to live trade signals in six simple steps — no setup, no card to start.
        </p>
        <div className="steps">
          <div className="step">
            <span className="step-num">1</span>
            <h3>Create your free account</h3>
            <p>Just an email and password. Live candlestick charts for every Hyperliquid coin unlock instantly — no card required.</p>
          </div>
          <div className="step">
            <span className="step-num">2</span>
            <h3>Chart any coin</h3>
            <p>Search any listed symbol, switch timeframes from 1 minute to 1 day, and overlay SMA, EMA &amp; Volume for free.</p>
          </div>
          <div className="step">
            <span className="step-num">3</span>
            <h3>Build your watchlist</h3>
            <p>Add the coins you want to track. This matters: trade signals are generated <strong>only</strong> for the coins on your watchlist.</p>
          </div>
          <div className="step">
            <span className="step-num">4</span>
            <h3>Follow signal strategies</h3>
            <p>Open the Signals page and follow the algorithmic strategies you like — Momentum, MACD Trend, Trend Rider, Breakouts and more. Your feed shows only the strategies you follow. On <strong>Pro</strong>, you can even <strong>describe your own strategy in a sentence and have AI build it</strong> for you.</p>
          </div>
          <div className="step">
            <span className="step-num">5</span>
            <h3>Read your signal feed</h3>
            <p>Each signal card gives the direction, entry, stop-loss and three take-profit targets (TP1–TP3), a conviction score, and a plain-English reason. Informational only — not financial advice.</p>
          </div>
          <div className="step">
            <span className="step-num">6</span>
            <h3>Get alerts on Telegram</h3>
            <p>On a paid plan, connect Telegram in one tap and new signals are pushed straight to your phone — plus a heads-up when a trade hits its target or stop.</p>
          </div>
        </div>
      </section>

      {/* Feature deep-dive */}
      <section id="features" className="features">
        <h2>Two markets, one workspace — with a signal engine built in</h2>
        <p className="section-sub">
          Live crypto and forex charts, 11 indicators, and an always-on engine that watches
          the strategies you follow and tells you the moment a setup fires.
          Start free; upgrade when you want the signals.
        </p>
        <div className="feature-grid">
          {/* The two cards that actually differentiate the product get hero
              treatment: double width, accent framing, and their specifics as a
              scannable list instead of a paragraph nobody finishes. */}
          <div className="feature feature-hero">
            <div className="feature-icon">🤖</div>
            <h3>Algorithmic trading signals <span className="feature-tag premium">Premium</span></h3>
            <p>An always-on engine scans the strategies you follow across <strong>both crypto and forex</strong>, and only surfaces the setups that clear its confidence bar — no firehose of noise.</p>
            <ul className="feature-points">
              <li>Entry, stop-loss and three take-profit targets (TP1–TP3)</li>
              <li>Risk/reward math, in percent and in dollars per $100 traded</li>
              <li>A plain-English reason the setup was flagged, and what invalidates it</li>
              <li>Pushed to <strong>Telegram</strong>, with an update when a target or stop is hit</li>
            </ul>
            <p className="feature-note">Informational only, never financial advice.</p>
          </div>
          <div className="feature feature-hero">
            <div className="feature-icon">🧠</div>
            <h3>Build your own strategy with AI <span className="feature-tag premium">Pro</span></h3>
            <p><strong>Describe a strategy in plain English</strong> — "buy when RSI drops below 30 and price is above the 200 EMA" — and the AI turns your words into a live strategy.</p>
            <ul className="feature-points">
              <li>Maps your intent to the right indicators, and shows you what it built before you save</li>
              <li>Then runs around the clock on your symbols, like any built-in strategy</li>
              <li>Its signals land in-app and on Telegram alongside the rest</li>
              <li>No code, no formulas, no backtesting knowledge needed</li>
            </ul>
            <p className="feature-note">Custom strategies aren't backtested; informational only, not financial advice.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">💱</div>
            <h3>Crypto + Forex <span className="feature-tag free">New</span></h3>
            <p>Flip between Hyperliquid crypto and the major FX pairs with one toggle — same charts, same indicators, same signals.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">📈</div>
            <h3>Real-time candlestick charts <span className="feature-tag free">Free</span></h3>
            <p>Live OHLC candles from 1m to 1d, drawn with TradingView's lightweight-charts. The feed auto-reconnects if your connection drops — no frozen charts.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🧮</div>
            <h3>11 technical indicators <span className="feature-tag">Free + Premium</span></h3>
            <p>SMA, EMA and Volume free forever. RSI, MACD, Bollinger Bands, Stochastic, ATR, VWAP, Fibonacci and Ichimoku on Premium — all computed in-browser, so they move tick-by-tick.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">⭐</div>
            <h3>Watchlists <span className="feature-tag free">Free</span></h3>
            <p>Star the coins and pairs you trade straight from the symbol search. Your list and workspace follow you across sessions and devices.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🔔</div>
            <h3>Price alerts <span className="feature-tag free">Free</span></h3>
            <p>Set price-cross alerts on any coin or pair and get notified the moment a level is hit — step away from the screen without missing the move.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">✏️</div>
            <h3>Drawing tools <span className="feature-tag free">Free</span></h3>
            <p>Trendlines, levels and shapes that stay pinned to price as you pan and zoom — your analysis is where you left it next time.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">💾</div>
            <h3>Saved chart layouts <span className="feature-tag premium">Premium</span></h3>
            <p>Save a symbol, timeframe and indicator preset together, then reload the exact setup in one click — one layout per trading style.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🌗</div>
            <h3>Dark &amp; light themes <span className="feature-tag free">Free</span></h3>
            <p>A clean, distraction-free interface built for long sessions. Switch any time — your preference is remembered.</p>
          </div>
        </div>
      </section>

      {/* Indicators showcase */}
      <section id="indicators" className="indicators-showcase">
        <h2>11 indicators, one click away</h2>
        <p className="section-sub">Free to start, with the advanced suite a tap away when you're ready.</p>
        <div className="indicator-columns">
          <div className="indicator-col">
            <h3>Free</h3>
            <div className="chip-row">
              {FREE_INDICATORS.map((i) => <span key={i} className="chip free">{i}</span>)}
            </div>
          </div>
          <div className="indicator-col">
            <h3>Premium 🔒</h3>
            <div className="chip-row">
              {PREMIUM_INDICATORS.map((i) => <span key={i} className="chip premium">{i}</span>)}
            </div>
          </div>
        </div>
      </section>

      {/* Pricing — hidden entirely for lifetime owners, who have nothing to buy. */}
      {!ownsLifetime && (
      <section id="pricing" className="pricing">
        <h2>Simple pricing</h2>
        <p className="section-sub">Priced to be the affordable alternative — start free, upgrade only if you want the advanced tools.</p>

        <div className="billing-toggle" role="tablist" aria-label="Billing period">
          <button
            role="tab"
            aria-selected={billing === "monthly"}
            className={billing === "monthly" ? "active" : ""}
            onClick={() => setBilling("monthly")}
          >
            Monthly
          </button>
          <button
            role="tab"
            aria-selected={billing === "lifetime"}
            className={billing === "lifetime" ? "active" : ""}
            onClick={() => setBilling("lifetime")}
          >
            Lifetime
            <span className="billing-toggle-tag">Best value</span>
          </button>
        </div>

        {billing === "monthly" ? (
          <div className="plan-grid">
            {plans.map((p) => {
              const isFree = p.price_usd === 0;
              const popular = p.key === "starter";
              // Logged-in users shouldn't be sent to signup: free → dashboard,
              // paid → the in-app billing/upgrade page.
              const ctaTo = !isAuthed ? "/signup" : isFree ? "/app" : "/account/billing";
              const ctaLabel = !isAuthed
                ? isFree ? "Get started" : "Start free, upgrade later"
                : isFree ? "Open dashboard →" : "Upgrade";
              return (
                <div key={p.key} className={`plan-card ${popular ? "featured" : ""}`}>
                  {popular && <span className="plan-badge">Most popular</span>}
                  <h3>{p.label}</h3>
                  <p className="plan-price">${p.price_usd}<span>/{p.period || "mo"}</span></p>
                  {p.tagline && <p className="plan-tagline muted">{p.tagline}</p>}
                  <ul>{p.features.map((f) => <li key={f}>✓ {f}</li>)}</ul>
                  <Link to={ctaTo} className={`btn-block ${popular ? "btn-primary" : "btn-ghost"}`}>
                    {ctaLabel}
                  </Link>
                </div>
              );
            })}
          </div>
        ) : (
          <div className="plan-grid plan-grid-single">
            <div className="plan-card featured plan-card-lifetime">
              <span className="plan-badge">Pay once, own it</span>
              <h3>{lifetime.label}</h3>
              <LifetimePrice plan={lifetime} />
              {breakEvenMonths && (
                <p className="plan-lifetime-compare muted">
                  Pays for itself in ~{breakEvenMonths} months at ${monthlyPro}/mo — then it's yours free.
                </p>
              )}
              {lifetime.tagline && <p className="plan-tagline muted">{lifetime.tagline}</p>}
              <ul>{lifetime.features.map((f) => <li key={f}>✓ {f}</li>)}</ul>
              <Link
                to={isAuthed ? "/account/billing" : "/signup"}
                className="btn-block btn-primary"
              >
                {isAuthed ? "Get lifetime access" : "Create an account to buy"}
              </Link>
            </div>
          </div>
        )}

        <p className="plan-note muted">
          {billing === "lifetime"
            ? "One payment, no subscription, no expiry — Pro features stay unlocked on your account for good."
            : "Premium billing is rolling out soon — create a free account today and upgrade in-app once it's live."}
        </p>
      </section>
      )}

      {/* FAQ */}
      <section id="faq" className="faq">
        <h2>Frequently asked</h2>
        <div className="faq-list">
          {FAQS.map((f) => (
            <details key={f.q} className="faq-item">
              <summary>{f.q}</summary>
              <p>{f.a}</p>
            </details>
          ))}
        </div>
      </section>

      {/* Final CTA */}
      <section className="final-cta">
        <h2>Ready to chart?</h2>
        {isAuthed ? (
          <>
            <p>Welcome back — jump straight into your charts.</p>
            <Link to="/app" className="btn-primary btn-lg">Open dashboard →</Link>
          </>
        ) : (
          <>
            <p>Create a free account and open your first live chart in under a minute.</p>
            <Link to="/signup" className="btn-primary btn-lg">Start charting free</Link>
          </>
        )}
      </section>
      </main>

      {/* Footer */}
      <footer className="landing-footer">
        <div className="footer-cols">
          <div className="footer-brand">
            <span className="brand"><Logo /></span>
            <p className="muted">Affordable crypto &amp; forex charting, powered by Hyperliquid and live FX data.</p>
          </div>
          <div className="footer-col">
            <h4>Product</h4>
            <a href="#features">Features</a>
            {!ownsLifetime && <a href="#pricing">Pricing</a>}
            <Link to="/app">Open app</Link>
          </div>
          <div className="footer-col">
            <h4>Account</h4>
            {isAuthed ? (
              <>
                <Link to="/app">Dashboard</Link>
                <Link to="/account/billing">Plan &amp; billing</Link>
                <button type="button" className="footer-linkbtn" onClick={logout}>Sign out</button>
              </>
            ) : (
              <>
                <Link to="/login">Sign in</Link>
                <Link to="/signup">Create account</Link>
                <Link to="/forgot-password">Reset password</Link>
              </>
            )}
          </div>
        </div>
        <div className="footer-bottom muted">
          PulseCharts is a charting tool for informational purposes only and is not
          financial advice. © {new Date().getFullYear()} MAILIONDEV TECHNOLOGY LTD (RC 9233525).
        </div>
      </footer>
    </div>
    <SupportChat />
    </>
  );
}
