// Post-payment landing (Paystack `callback_url` → /billing/success). The plan is
// granted by the webhook, not this redirect, so the webhook may not have landed
// the instant the user gets back here. We poll entitlements for a short window
// and confirm once the upgrade shows up, instead of bouncing to the landing page.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { useStore } from "../store/useStore";
import Logo from "../components/Logo";

export default function BillingSuccessPage() {
  const entitlements = useStore((s) => s.entitlements);
  const loadEntitlements = useStore((s) => s.loadEntitlements);
  const isPremium = entitlements?.is_premium;

  // "activating" = still polling for the webhook to confirm the upgrade.
  const [activating, setActivating] = useState(!isPremium);

  useEffect(() => {
    let tries = 0;
    let timer;
    async function poll() {
      await loadEntitlements();
      tries += 1;
      // Stop once premium shows up, or after ~30s (10 tries × 3s).
      if (useStore.getState().entitlements?.is_premium || tries >= 10) {
        setActivating(false);
        return;
      }
      timer = setTimeout(poll, 3000);
    }
    poll();
    return () => clearTimeout(timer);
  }, [loadEntitlements]);

  const planLabel = entitlements?.plan_label || "your new plan";

  return (
    <div className="billing-result-page">
      <Link to="/" className="brand br-brand"><Logo /></Link>
      <div className="billing-result-card">
        {isPremium ? (
          <>
            <div className="br-icon ok">✓</div>
            <h1>You’re on {planLabel}!</h1>
            <p className="muted">
              Payment successful and your plan is active. Your upgraded indicators,
              strategies and limits are unlocked.
            </p>
            <Link to="/app" className="btn-primary btn-lg">Go to charts</Link>
          </>
        ) : activating ? (
          <>
            <div className="br-icon spin">◌</div>
            <h1>Activating your plan…</h1>
            <p className="muted">
              Payment received. We’re confirming it with our payment provider —
              this usually takes a few seconds.
            </p>
          </>
        ) : (
          <>
            <div className="br-icon ok">✓</div>
            <h1>Payment received</h1>
            <p className="muted">
              Thanks! Your upgrade is being processed and will activate shortly. If it
              doesn’t appear in a minute, refresh or check your billing page.
            </p>
            <div className="br-actions">
              <button className="btn-primary" onClick={() => loadEntitlements()}>
                Refresh status
              </button>
              <Link to="/account/billing" className="btn-ghost">Billing</Link>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
