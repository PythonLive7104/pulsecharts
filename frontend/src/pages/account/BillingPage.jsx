// Plan & Billing — current plan, expiry, and the three-tier upgrade grid
// (Section 12, 16). Paystack billing: if it isn't configured (no secret key),
// upgrade returns a clean "coming soon" — surfaced here without looking broken.
import { useEffect, useState } from "react";
import { useStore } from "../../store/useStore";
import { api } from "../../api";
import { LIFETIME_FALLBACK, PLAN_FALLBACK, isLifetime, planNeverExpires } from "../../lib/plans";
import LifetimePrice from "../../components/LifetimePrice";

export default function BillingPage() {
  const entitlements = useStore((s) => s.entitlements);
  const loadEntitlements = useStore((s) => s.loadEntitlements);
  const isPremium = entitlements?.is_premium;
  const expiry = entitlements?.plan_expiry;
  const currentKey = entitlements?.plan_key || "free";
  // Lifetime BUYERS have nothing left to buy — the plan grid hides for them only.
  const ownsLifetime = isLifetime(entitlements);
  // Anyone whose access never expires (incl. staff-granted Pro) can't redeem a
  // code or credits without downgrading themselves — the API refuses it.
  const neverExpires = planNeverExpires(entitlements);

  const [plans, setPlans] = useState(PLAN_FALLBACK);
  const [lifetime, setLifetime] = useState(LIFETIME_FALLBACK);
  const [billing, setBilling] = useState("monthly"); // "monthly" | "lifetime"
  const [notice, setNotice] = useState(null);
  const [busy, setBusy] = useState(false);
  const [history, setHistory] = useState(null);

  // Referral / earnings
  const [ref, setRef] = useState(null);
  const [refMsg, setRefMsg] = useState(null);
  const [editingCode, setEditingCode] = useState(false);
  const [newCode, setNewCode] = useState("");

  // Admin Pro promo code (self-upgrade to Pro to trial premium)
  const [promoCode, setPromoCode] = useState("");
  const [promoMsg, setPromoMsg] = useState(null);
  const [promoOk, setPromoOk] = useState(false);
  const [promoBusy, setPromoBusy] = useState(false);

  function loadRef() {
    api.referral().then(setRef).catch(() => {});
  }

  useEffect(() => {
    // Entitlements drive the current-plan pill, the "Current plan" ribbon, and
    // whether the redeem/pricing surfaces show. The store starts null and isn't
    // persisted, so a hard refresh landing directly here would otherwise read
    // every fallback as Free and offer a paying user the upgrade grid.
    loadEntitlements();
    api.plans()
      .then((d) => {
        if (d?.plans?.length) setPlans(d.plans);
        if (d?.lifetime) setLifetime(d.lifetime);
      })
      .catch(() => { /* keep fallback */ });
    api.billingHistory().then(setHistory).catch(() => setHistory([]));
    loadRef();
  }, [loadEntitlements]);

  function copyShareLink() {
    if (ref?.share_url) {
      navigator.clipboard?.writeText(ref.share_url);
      setRefMsg("Referral link copied to clipboard.");
    }
  }

  async function saveCode() {
    setRefMsg(null);
    try {
      await api.referralSetCode(newCode);
      setEditingCode(false);
      loadRef();
      setRefMsg("Your referral code was updated.");
    } catch (e) {
      setRefMsg(e.message);
    }
  }

  async function redeemCredits(plan) {
    setRefMsg(null);
    try {
      await api.referralRedeem(plan);
      await loadEntitlements();
      loadRef();
      setRefMsg(`Redeemed — ${plan === "pro" ? "Pro" : "Starter"} is active for 30 days.`);
    } catch (e) {
      setRefMsg(e.message);
    }
  }

  async function redeemPromo() {
    setPromoMsg(null);
    setPromoOk(false);
    setPromoBusy(true);
    try {
      const res = await api.redeemPromoCode(promoCode);
      await loadEntitlements();
      const until = res?.plan_expiry ? new Date(res.plan_expiry).toLocaleDateString() : null;
      const planLabel = res?.plan_tier === "pro" ? "Pro" : res?.plan_tier === "starter" ? "Starter" : "Premium";
      setPromoOk(true);
      setPromoMsg(`${planLabel} unlocked${until ? ` until ${until}` : ""} — enjoy the premium features!`);
      setPromoCode("");
    } catch (e) {
      setPromoMsg(e.message);
    } finally {
      setPromoBusy(false);
    }
  }

  async function upgrade(plan) {
    setBusy(true);
    setNotice(null);
    try {
      const session = await api.checkout(plan);
      if (session?.checkout_url) window.location.href = session.checkout_url;
    } catch (e) {
      // 503 coming-soon (BILLING_LIVE false) or other — show a friendly message.
      setNotice(
        e.status === 503
          ? "Premium billing is coming soon — we're finishing payment setup. Check back shortly!"
          : e.message
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="account-pages">
      <h1>Plan &amp; Billing</h1>

      <div className="card">
        <h2>Current plan</h2>
        <div className="plan-status">
          <span className={`plan-badge-lg ${isPremium ? "premium" : ""}`}>
            {ownsLifetime
              ? `${entitlements?.plan_label || "Pro"} · Lifetime`
              : entitlements?.plan_label || (isPremium ? "Premium" : "Free")}
          </span>
          {neverExpires ? (
            <span className="muted">Never expires</span>
          ) : isPremium && expiry ? (
            /* "Renews" was wrong: billing is one-time payments with a timed grant —
               nothing auto-renews, so this date is when access ENDS. */
            <span className="muted">Access until {new Date(expiry).toLocaleDateString()}</span>
          ) : null}
        </div>
        <p className="muted">
          {ownsLifetime
            ? "You own PulseCharts Pro for life — every indicator, strategy and layout stays unlocked, with nothing left to renew."
            : neverExpires
              ? "Your plan has no expiry date — full access to your indicators, strategies and saved layouts."
              : isPremium
                ? "You have full access to your plan's indicators, strategies and saved layouts."
                : "Live charts, all timeframes, and SMA/EMA/Volume are free forever. Upgrade for advanced indicators, more strategies and a bigger watchlist."}
        </p>
      </div>

      {!neverExpires && (
      <div className="card">
        <h2>Have an access code?</h2>
        <p className="muted">
          Got an invite code? Redeem it to unlock a <strong>premium plan</strong> and trial the
          premium features — including building your own AI strategies.
          One code per account.
        </p>
        <div className="promo-redeem">
          <input
            className="promo-input"
            type="text"
            placeholder="Enter your code"
            value={promoCode}
            onChange={(e) => { setPromoCode(e.target.value); setPromoMsg(null); }}
            onKeyDown={(e) => { if (e.key === "Enter" && promoCode.trim()) redeemPromo(); }}
          />
          <button
            className="btn-primary"
            onClick={redeemPromo}
            disabled={promoBusy || !promoCode.trim()}
          >
            {promoBusy ? "Redeeming…" : "Redeem"}
          </button>
        </div>
        {promoMsg && <p className={promoOk ? "success" : "error"}>{promoMsg}</p>}
      </div>
      )}

      {/* Pricing is pointless for a lifetime owner — hide the whole card. */}
      {!ownsLifetime && (
        <div className="card">
          <h2>Choose your plan</h2>

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
            <div className="plan-grid billing-plan-grid">
              {plans.map((p) => {
                const isFree = p.price_usd === 0;
                const isCurrent = p.key === currentKey;
                const popular = p.key === "starter";
                return (
                  <div
                    key={p.key}
                    className={`plan-card ${popular ? "featured" : ""} ${isCurrent ? "current" : ""}`}
                  >
                    {isCurrent ? (
                      <span className="plan-badge">Current plan</span>
                    ) : popular ? (
                      <span className="plan-badge">Most popular</span>
                    ) : null}
                    <h3>{p.label}</h3>
                    <p className="plan-price">${p.price_usd}<span>/{p.period || "mo"}</span></p>
                    {p.tagline && <p className="plan-tagline muted">{p.tagline}</p>}
                    <ul>{p.features.map((f) => <li key={f}>✓ {f}</li>)}</ul>
                    {isCurrent ? (
                      <button className="btn-ghost btn-block" disabled>Your plan</button>
                    ) : isFree ? (
                      <button className="btn-ghost btn-block" disabled>Included</button>
                    ) : (
                      <button className="btn-primary btn-block" onClick={() => upgrade(p.key)} disabled={busy}>
                        {busy ? "…" : `Upgrade to ${p.label}`}
                      </button>
                    )}
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
                {lifetime.tagline && <p className="plan-tagline muted">{lifetime.tagline}</p>}
                <ul>{lifetime.features.map((f) => <li key={f}>✓ {f}</li>)}</ul>
                <button
                  className="btn-primary btn-block"
                  onClick={() => upgrade("lifetime")}
                  disabled={busy}
                >
                  {busy ? "…" : `Get lifetime access — $${lifetime.price_usd}`}
                </button>
              </div>
            </div>
          )}
          {notice && <p className="muted notice">{notice}</p>}
        </div>
      )}

      {ref && (
        <div className="card">
          <h2>Refer &amp; earn</h2>
          <p className="muted">
            Share your code — you earn ${ref.reward_per_referral} every time someone signs up with it.
            {neverExpires
              ? " Your access never expires, so there's no plan left to redeem — your balance just keeps growing."
              : ` Cash your balance in for a plan: $${ref.prices.starter} → Starter, $${ref.prices.pro} → Pro (30 days each).`}
          </p>

          <div className="referral-grid">
            <div className="referral-code">
              <span className="muted">Your code</span>
              {editingCode ? (
                <div className="referral-edit">
                  <input
                    value={newCode}
                    onChange={(e) => setNewCode(e.target.value.toUpperCase())}
                    placeholder="e.g. MAILIONDEV_7788"
                  />
                  <button className="btn-primary" onClick={saveCode}>Save</button>
                  <button className="btn-ghost" onClick={() => setEditingCode(false)}>Cancel</button>
                </div>
              ) : (
                <div className="referral-edit">
                  <code>{ref.code}</code>
                  <button className="btn-ghost" onClick={() => { setNewCode(ref.code); setEditingCode(true); }}>
                    Edit
                  </button>
                </div>
              )}
            </div>
            <div className="referral-stats">
              <div><strong>${ref.credits}</strong><span className="muted">credits</span></div>
              <div><strong>{ref.referred_count}</strong><span className="muted">referrals</span></div>
            </div>
          </div>

          <div className="referral-share">
            <input readOnly value={ref.share_url} onFocus={(e) => e.target.select()} />
            <button className="btn-ghost" onClick={copyShareLink}>Copy link</button>
          </div>

          {/* A timed plan grant would replace never-expiring access with a 30-day
              expiry — the API refuses it, so don't offer it. */}
          {!neverExpires && (
            <div className="referral-redeem">
              <button className="btn-primary" disabled={!ref.can_redeem_starter}
                onClick={() => redeemCredits("starter")}>
                Redeem ${ref.prices.starter} → Starter
              </button>
              <button className="btn-primary" disabled={!ref.can_redeem_pro}
                onClick={() => redeemCredits("pro")}>
                Redeem ${ref.prices.pro} → Pro
              </button>
            </div>
          )}
          {refMsg && <p className="muted notice">{refMsg}</p>}

          {/* Commissions: a SECOND, separate ledger. Credits above are the $1 signup
              reward and are spent in-app; this is a cash share of what referred users
              actually paid, settled outside the product and marked paid by an admin. */}
          {ref.commission?.rate_pct > 0 && (
            <div className="commission">
              <div className="commission-head">
                <h3>Subscription commissions</h3>
                <span className="muted">
                  {ref.commission.rate_pct}% of every payment made by someone who
                  signed up with your code
                </span>
              </div>

              {ref.commission.items?.length ? (
                <>
                  <div className="commission-stats">
                    <div>
                      <strong>${ref.commission.pending_usd}</strong>
                      <span className="muted">awaiting payout</span>
                    </div>
                    <div>
                      <strong>${ref.commission.paid_usd}</strong>
                      <span className="muted">paid out</span>
                    </div>
                    <div>
                      <strong>{ref.commission.count}</strong>
                      <span className="muted">paid referrals</span>
                    </div>
                  </div>
                  <div className="commission-rows">
                    {ref.commission.items.map((c) => (
                      <div key={c.id} className="cm-row">
                        <span className="cm-date muted">
                          {new Date(c.date).toLocaleDateString()}
                        </span>
                        <span className="cm-who">{c.email}</span>
                        <span className="cm-plan muted">{c.plan}</span>
                        <span className="cm-amt muted">of ${c.amount_usd}</span>
                        <span className="cm-earn"><b>${c.commission_usd}</b></span>
                        <span className={`cm-status cm-${c.status.toLowerCase()}`}>
                          {c.status === "PAID"
                            ? `Paid${c.paid_at ? ` ${new Date(c.paid_at).toLocaleDateString()}` : ""}`
                            : c.status === "VOID" ? "Refunded" : "Pending"}
                        </span>
                      </div>
                    ))}
                  </div>
                  <p className="muted commission-note">
                    Payouts are sent manually and marked here once the money has left.
                  </p>
                </>
              ) : (
                <div className="commission-empty">
                  <span className="ce-icon" aria-hidden="true">💸</span>
                  <p><b>No one you referred has subscribed yet.</b></p>
                  <p className="muted">
                    You earn <b>{ref.commission.rate_pct}%</b> of every payment they
                    make — ${(ref.prices.pro * ref.commission.rate_pct / 100).toFixed(2)} on
                    a Pro plan, ${(ref.prices.starter * ref.commission.rate_pct / 100).toFixed(2)} on
                    Starter — for as long as they keep paying. Share your link and keep pushing.
                  </p>
                  <button className="btn-ghost" onClick={copyShareLink}>Copy your link</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      <div className="card">
        <h2>Billing history</h2>
        {history == null ? (
          <p className="muted">Loading…</p>
        ) : history.length === 0 ? (
          <p className="muted">No invoices yet.</p>
        ) : (
          <div className="billing-history">
            {history.map((h) => (
              <div key={h.id} className="bh-row">
                <span className="bh-plan">{h.tier_label}</span>
                <span className={`bh-status bh-${h.status}`}>{h.status_label}</span>
                <span className="muted bh-date">
                  Started {new Date(h.created_at).toLocaleDateString()}
                </span>
                <span className="muted bh-date">
                  {h.renewal_date
                    ? `Access until ${new Date(h.renewal_date).toLocaleDateString()}`
                    : "—"}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
