// Checkout cancelled (Paystack redirect → /billing/cancel). No charge was made.
import { Link } from "react-router-dom";
import Logo from "../components/Logo";

export default function BillingCancelPage() {
  return (
    <div className="billing-result-page">
      <Link to="/" className="brand br-brand"><Logo /></Link>
      <div className="billing-result-card">
        <div className="br-icon warn">!</div>
        <h1>Checkout cancelled</h1>
        <p className="muted">
          No payment was taken. You can pick a plan whenever you’re ready — nothing has changed
          on your account.
        </p>
        <Link to="/account/billing" className="btn-primary btn-lg">Back to plans</Link>
      </div>
    </div>
  );
}
