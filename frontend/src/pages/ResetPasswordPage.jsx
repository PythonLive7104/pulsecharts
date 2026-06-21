import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import AuthCard from "../components/AuthCard";

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const uid = params.get("uid");
  const token = params.get("token");
  const navigate = useNavigate();

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const invalidLink = !uid || !token;

  async function submit(e) {
    e.preventDefault();
    if (password !== confirm) {
      setError("Passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api.confirmPasswordReset(uid, token, password);
      setDone(true);
      setTimeout(() => navigate("/login"), 1500);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Set a new password"
      footer={<Link to="/login">Back to sign in</Link>}
    >
      {invalidLink ? (
        <p className="error">This reset link is missing or invalid.</p>
      ) : done ? (
        <p className="success">Password updated. Redirecting to sign in…</p>
      ) : (
        <form onSubmit={submit} className="auth-form">
          <label>
            New password
            <input type="password" value={password} required autoFocus minLength={8}
              onChange={(e) => setPassword(e.target.value)} />
          </label>
          <label>
            Confirm new password
            <input type="password" value={confirm} required
              onChange={(e) => setConfirm(e.target.value)} />
          </label>
          {error && <p className="error">{error}</p>}
          <button type="submit" className="btn-primary btn-block" disabled={busy}>
            {busy ? "Saving…" : "Reset password"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}
