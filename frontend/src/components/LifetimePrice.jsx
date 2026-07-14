// Lifetime price with its discount: struck-through original + "N% off" badge.
//
// Everything is read from the plan payload (/api/plans/ -> LIFETIME_PLAN), never
// hardcoded — the badge is derived from the same numbers checkout charges, so the
// advertised saving can't drift from the real one. If the backend ever drops the
// discount fields, this degrades to a plain price rather than a stale "25% off".
export default function LifetimePrice({ plan }) {
  const price = plan?.price_usd;
  const was = plan?.original_price_usd;
  const off = plan?.discount_pct;
  const discounted = was != null && was > price;

  return (
    <p className="plan-price lifetime-price">
      {discounted && <span className="price-was">${was}</span>}
      <span className="price-now">${price}</span>
      <span>&nbsp;once</span>
      {discounted && off ? <span className="price-off">{off}% off</span> : null}
    </p>
  );
}
