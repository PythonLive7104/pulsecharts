import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useStore } from "../store/useStore";
import { api } from "../api";
import AuthCard from "../components/AuthCard";
import PasswordInput from "../components/PasswordInput";

// The backend rejects unverified logins with this code (VerifiedTokenObtainPairSerializer).
function isUnverified(err) {
  const code = err?.data?.code;
  return (Array.isArray(code) ? code[0] : code) === "email_not_verified";
}

export default function LoginPage() {
  const login = useStore((s) => s.login);
  const navigate = useNavigate();
  const location = useLocation();
  const dest = location.state?.from || "/app"; // return to gated page if redirected
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [needsVerify, setNeedsVerify] = useState(false);
  const [resendMsg, setResendMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setNeedsVerify(false);
    setResendMsg(null);
    try {
      await login(email, password);
      navigate(dest, { replace: true });
    } catch (err) {
      if (isUnverified(err)) {
        // Correct credentials, but the email isn't verified — offer a resend rather
        // than a dead-end error.
        setNeedsVerify(true);
      } else {
        setError(err.status === 401 ? "Invalid email or password." : err.message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setResendMsg(null);
    try {
      await api.resendVerification(email);
      setResendMsg("Verification email sent — check your inbox (and spam).");
    } catch {
      setResendMsg("Couldn't resend just now. Please try again shortly.");
    }
  }

  return (
    <AuthCard
      title="Welcome back"
      subtitle="Sign in to your PulseCharts account"
      footer={
        <span>
          No account? <Link to="/signup">Create one</Link>
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
          <PasswordInput value={password} autoComplete="current-password"
            placeholder="Your password"
            onChange={(e) => setPassword(e.target.value)} />
        </label>
        <div className="auth-row-end">
          <Link to="/forgot-password" className="link-muted">Forgot password?</Link>
        </div>
        {error && <p className="error">{error}</p>}
        {needsVerify && (
          <div className="verify-notice">
            <p>
              📩 Please verify your email before signing in. We sent a link to{" "}
              <strong>{email}</strong> when you signed up.
            </p>
            <button type="button" className="btn-ghost btn-block" onClick={resend}>
              Resend verification email
            </button>
            {resendMsg && <p className="auth-hint">{resendMsg}</p>}
          </div>
        )}
        <button type="submit" className="btn-primary btn-block" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </AuthCard>
  );
}
