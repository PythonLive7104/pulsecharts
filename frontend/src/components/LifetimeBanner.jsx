import { useState } from "react";
import { Link } from "react-router-dom";

/**
 * Top-of-page promo bar for Pro Lifetime.
 *
 * Every number is read from the plan payload, never hardcoded — same contract as
 * LifetimePrice. The struck-through price shows ONLY when the backend actually sets
 * `original_price_usd`, so the bar can never advertise a discount that checkout isn't
 * charging. With no discount configured it falls back to the break-even framing, which
 * is a real comparison a visitor can verify rather than an invented "was" price.
 *
 * Dismissal is per-view state only, deliberately NOT persisted: closing it clears the
 * bar for the current read, and a refresh or a later visit shows the offer again.
 *
 * Rendered above the sticky nav, so it scrolls away instead of permanently eating
 * viewport (and #anchor scroll-margin stays correct against the nav alone).
 */
export default function LifetimeBanner({ plan, monthlyPrice, breakEvenMonths, isAuthed }) {
  const price = plan?.price_usd;
  const was = plan?.original_price_usd;
  const off = plan?.discount_pct;
  const discounted = was != null && price != null && was > price;

  const [hidden, setHidden] = useState(false);

  if (hidden || price == null) return null;

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
          onClick={() => setHidden(true)}
          aria-label="Dismiss lifetime offer"
        >
          ×
        </button>
      </div>
    </div>
  );
}
