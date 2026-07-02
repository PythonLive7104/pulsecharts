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
import { PLAN_FALLBACK } from "../lib/plans";

// Lazy so three.js + R3F load in their own chunk only on the landing page,
// keeping the trading app bundle lean.
const ThreeScene = lazy(() => import("../components/hero3d/ThreeScene"));

const FREE_INDICATORS = ["SMA", "EMA", "Volume"];
const PREMIUM_INDICATORS = [
  "RSI", "MACD", "Bollinger Bands", "Stochastic",
  "ATR", "Fibonacci", "VWAP", "Ichimoku Cloud",
];

const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];

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
    a: "Both. We chart the perps and spot pairs listed on Hyperliquid, plus the major forex pairs (EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD). Switch between crypto and forex with one toggle — the same indicators and trading signals work across both.",
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
  const isAuthed = useStore((s) => s.isAuthed);
  const [plans, setPlans] = useState(PLAN_FALLBACK);
  useEffect(() => {
    api.plans()
      .then((d) => { if (d?.plans?.length) setPlans(d.plans); })
      .catch(() => { /* keep fallback */ });
  }, []);

  return (
    <>
      <header className="landing-nav">
        <div className="landing-nav-inner">
          <span className="brand"><Logo /></span>
          <nav className="landing-nav-links">
          <a href="#how">How it works</a>
          <a href="#features">Features</a>
          <a href="#indicators">Indicators</a>
          <a href="#pricing">Pricing</a>
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
            <h1 className="hero-anim">
              Pro-grade crypto &amp; forex <span className="grad">charts</span>,<br />
              without the pro-grade price.
            </h1>
            <p className="hero-sub hero-anim">
              Real-time candlestick charting for Hyperliquid crypto and the major
              forex pairs — switch between them in one click. Start free with live
              charts and core indicators; upgrade for advanced analysis, saved
              layouts, and trading signals on both — including strategies you
              build yourself, just by describing them to our AI.
            </p>
            <div className="hero-cta hero-anim">
              {isAuthed ? (
                <Link to="/app" className="btn-primary btn-lg">Open dashboard →</Link>
              ) : (
                <>
                  <Link to="/signup" className="btn-primary btn-lg">Start charting free</Link>
                  <Link to="/login" className="btn-ghost btn-lg">Sign in →</Link>
                </>
              )}
            </div>
            <p className="hero-note hero-anim">No card required · Crypto &amp; Forex · Cancel anytime</p>
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
            <p>Each signal card gives the direction, entry, stop-loss and four take-profit targets (TP1–TP4), a conviction score, and a plain-English reason. Informational only — not financial advice.</p>
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
        <h2>Everything a crypto &amp; forex trader needs</h2>
        <p className="section-sub">
          A focused, fast charting workspace — live data, the indicators that matter,
          algorithmic signals and the tools to act on them. Start free, upgrade when you want more.
        </p>
        <div className="feature-grid">
          <div className="feature">
            <div className="feature-icon">📈</div>
            <h3>Real-time candlestick charts <span className="feature-tag free">Free</span></h3>
            <p>Live OHLC candles for every Hyperliquid-listed coin <strong>and the major forex pairs</strong>, drawn with TradingView's lightweight-charts. Flip between 1m and 1d timeframes instantly, and the feed auto-reconnects if your connection drops — no frozen charts.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🧮</div>
            <h3>11 technical indicators <span className="feature-tag">Free + Premium</span></h3>
            <p>SMA, EMA and Volume free forever. Unlock RSI, MACD, Bollinger Bands, Stochastic, ATR, VWAP, Fibonacci and Ichimoku Cloud on Premium — all computed in your browser against the live candle buffer, so they update tick-by-tick with zero lag.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🤖</div>
            <h3>Algorithmic trading signals <span className="feature-tag premium">Premium</span></h3>
            <p>An always-on engine scans the strategies you follow — across <strong>both crypto and forex</strong> — and surfaces buy/sell signals, each with an entry, a stop-loss, four take-profit targets (TP1–TP4), risk/reward math and a plain-English reason it was flagged. Get them in-app or pushed straight to your <strong>Telegram</strong>, with trade updates when a target or stop is hit. Informational only, never financial advice.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🧠</div>
            <h3>Build your own strategy with AI <span className="feature-tag premium">Pro</span></h3>
            <p><strong>Describe a strategy in plain English</strong> — "buy when RSI drops below 30 and price is above the 200 EMA" — and our <strong>AI turns your words into a live trading strategy</strong>. It reads your intent, maps it to the right indicators (EMAs, RSI, MACD, Bollinger Bands, ADX, VWAP and more), and shows you exactly what it built before you save it. From then on the engine watches your coins around the clock and sends you its signals — in-app and on Telegram — just like the built-in strategies. No code, no formulas. Custom strategies aren't backtested; informational only, not financial advice.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🔔</div>
            <h3>Price alerts</h3>
            <p>Set price-cross alerts on any coin and get notified the moment a level is hit — so you can step away from the screen and still catch the move you were waiting for.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">✏️</div>
            <h3>Drawing tools</h3>
            <p>Mark up charts with trendlines, levels and shapes. Annotations stay pinned to price as you pan and zoom, so your analysis is exactly where you left it next time.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">💾</div>
            <h3>Saved chart layouts <span className="feature-tag premium">Premium</span></h3>
            <p>Save any combination of symbol, timeframe and indicator preset, then reload your exact setup in one click. Keep multiple layouts for different coins and trading styles and pick up right where you left off.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">⭐</div>
            <h3>Watchlists <span className="feature-tag free">Free</span></h3>
            <p>Build a watchlist of the coins you actually trade and flip between them without losing your indicators or drawings. Reorder freely; your list and workspace sync across sessions and devices.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">🌗</div>
            <h3>Dark & light themes <span className="feature-tag free">Free</span></h3>
            <p>A clean, distraction-free interface built for long sessions. Switch between dark and light any time — your preference is remembered the next time you open the app.</p>
          </div>
          <div className="feature">
            <div className="feature-icon">💱</div>
            <h3>Crypto + Forex <span className="feature-tag free">New</span></h3>
            <p>Trade two markets in one fast workspace. Flip between Hyperliquid crypto and the major forex pairs (EUR/USD, GBP/USD, USD/JPY and more) with a single toggle — same charts, same indicators, same signals. Responsive on desktop and your phone.</p>
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

      {/* Pricing */}
      <section id="pricing" className="pricing">
        <h2>Simple pricing</h2>
        <p className="section-sub">Priced to be the affordable alternative — start free, upgrade only if you want the advanced tools.</p>
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
        <p className="plan-note muted">
          Premium billing is rolling out soon — create a free account today and upgrade in-app once it's live.
        </p>
      </section>

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
            <a href="#pricing">Pricing</a>
            <Link to="/app">Open app</Link>
          </div>
          <div className="footer-col">
            <h4>Account</h4>
            <Link to="/login">Sign in</Link>
            <Link to="/signup">Create account</Link>
            <Link to="/forgot-password">Reset password</Link>
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
