import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useStore } from "../store/useStore";
import { api } from "../api";
import AuthCard from "../components/AuthCard";
import PasswordInput from "../components/PasswordInput";

export default function SignupPage() {
  const register = useStore((s) => s.register);
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  // Pre-fill the referral code from a ?ref=CODE link if present.
  const [referral, setReferral] = useState(
    () => new URLSearchParams(location.search).get("ref") || ""
  );
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  // Set once signup succeeds — the account exists but is unverified, so instead of
  // entering the app we show a "check your inbox" screen (no token is issued until
  // the emailed link is clicked).
  const [submitted, setSubmitted] = useState(false);
  const [resendMsg, setResendMsg] = useState(null);

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
      await register(email, password, referral.trim());
      setSubmitted(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setResendMsg(null);
    try {
      await api.resendVerification(email);
      setResendMsg("Sent — check your inbox again (and your spam folder).");
    } catch {
      setResendMsg("Couldn't resend just now. Please try again shortly.");
    }
  }

  if (submitted) {
    return (
      <AuthCard
        title="Check your inbox ✉️"
        subtitle="One quick step to activate your account"
        footer={<span>Already verified? <Link to="/login">Sign in</Link></span>}
      >
        <div className="verify-sent">
          <p>
            We sent a verification link to <strong>{email}</strong>. Click it to
            activate your account, then sign in.
          </p>
          <p className="muted">
            The link expires shortly. Didn't get it? Check your spam folder, or
            resend it below.
          </p>
          <button type="button" className="btn-ghost btn-block" onClick={resend}>
            Resend verification email
          </button>
          {resendMsg && <p className="auth-hint">{resendMsg}</p>}
        </div>
      </AuthCard>
    );
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
