import { useState } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { useStore } from "../store/useStore";
import AuthCard from "../components/AuthCard";
import PasswordInput from "../components/PasswordInput";

export default function LoginPage() {
  const login = useStore((s) => s.login);
  const navigate = useNavigate();
  const location = useLocation();
  const dest = location.state?.from || "/app"; // return to gated page if redirected
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(dest, { replace: true });
    } catch (err) {
      setError(err.status === 401 ? "Invalid email or password." : err.message);
    } finally {
      setBusy(false);
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
        <button type="submit" className="btn-primary btn-block" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </AuthCard>
  );
}
