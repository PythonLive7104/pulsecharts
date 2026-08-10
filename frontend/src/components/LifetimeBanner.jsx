import { useState } from "react";
import { Link } from "react-router-dom";

// Dismissal is remembered so the banner doesn't nag a returning visitor on every
// load. Keyed by price: if the offer changes, everyone sees it again rather than
// staying dismissed against a banner they never actually read.
const KEY = "pc_lifetime_banner_dismissed";

/**
 * Top-of-page promo bar for Pro Lifetime.
 *
 * Every number is read from the plan payload, never hardcoded — same contract as
 * LifetimePrice. The struck-through price shows ONLY when the backend actually sets
 * `original_price_usd`, so the bar can never advertise a discount that checkout isn't
 * charging. With no discount configured it falls back to the break-even framing, which
 * is a real comparison a visitor can verify rather than an invented "was" price.
 *
 * Rendered above the sticky nav, so it scrolls away instead of permanently eating
 * viewport (and #anchor scroll-margin stays correct against the nav alone).
 */
export default function LifetimeBanner({ plan, monthlyPrice, breakEvenMonths, isAuthed }) {
  const price = plan?.price_usd;
  const was = plan?.original_price_usd;
  const off = plan?.discount_pct;
  const discounted = was != null && price != null && was > price;

  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(KEY) === String(price);
    } catch {
      return false; // private mode / storage blocked — just show it
    }
  });

  if (hidden || price == null) return null;

  const dismiss = () => {
    try {
      localStorage.setItem(KEY, String(price));
    } catch {
      /* non-fatal: the banner simply reappears next visit */
    }
    setHidden(true);
  };

  return (
    <div className="lt-banner">
      <div className="lt-banner-inner">
        <p className="lt-banner-copy">
          <span className="lt-banner-tag">Pro Lifetime</span>
          {discounted ? (
            <>
              <s className="lt-banner-was">${was}</s>{" "}
              <b className="lt-banner-price">${price}</b> once
              {off ? <span className="lt-banner-off">{off}% off</span> : null}
            </>
          ) : (
            <>
              <b className="lt-banner-price">${price}</b> once — never renews, never
              expires
            </>
          )}
          {breakEvenMonths ? (
            <span className="lt-banner-sub">
              About {breakEvenMonths} months of Pro at ${monthlyPrice}/mo, then it's
              yours free.
            </span>
          ) : null}
        </p>
        <Link
          to={isAuthed ? "/account/billing" : "/signup"}
          className="lt-banner-cta"
        >
          {isAuthed ? "Get lifetime access" : "Claim it"} →
        </Link>
        <button
          type="button"
          className="lt-banner-close"
          onClick={dismiss}
          aria-label="Dismiss lifetime offer"
        >
          ×
        </button>
      </div>
    </div>
  );
}
