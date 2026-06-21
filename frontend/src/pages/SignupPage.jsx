import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useStore } from "../store/useStore";
import AuthCard from "../components/AuthCard";
import PasswordInput from "../components/PasswordInput";

export default function SignupPage() {
  const register = useStore((s) => s.register);
  const navigate = useNavigate();
  const location = useLocation();
  const dest = location.state?.from || "/app";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  // Pre-fill the referral code from a ?ref=CODE link if present.
  const [referral, setReferral] = useState(
    () => new URLSearchParams(location.search).get("ref") || ""
  );
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    if (password.length < 8) {
      setError("Password must be at least 8 characters.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await register(email, password, referral.trim()); // registers then logs in
      navigate(dest, { replace: true });
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Create your account"
      subtitle="Free live crypto charts — no card required"
      footer={
        <span>
          Already have an account? <Link to="/login">Sign in</Link>
        </span>
      }
    >
      <form onSubmit={submit} className="auth-form">
        <label>
          Email
          <input type="email" value={email} required autoFocus autoComplete="email"
            placeholder="you@example.com"
            onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label>
          Password
          <PasswordInput value={password} autoComplete="new-password" minLength={8}
            placeholder="At least 8 characters"
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        <label>
          Confirm password
          <PasswordInput value={confirm} autoComplete="new-password"
            placeholder="Re-enter password"
            onChange={(e) => setConfirm(e.target.value)} />
        </label>
        <label>
          Referral code <span className="label-optional">— optional</span>
          <input type="text" value={referral} autoComplete="off"
            placeholder="Have a code? Get 30 days of Starter free"
            onChange={(e) => setReferral(e.target.value)} />
        </label>
        <p className="auth-hint">Use at least 8 characters. No card required to start.</p>
        {error && <p className="error">{error}</p>}
        <button type="submit" className="btn-primary btn-block" disabled={busy}>
          {busy ? "Creating…" : "Create account"}
        </button>
      </form>
    </AuthCard>
  );
}
