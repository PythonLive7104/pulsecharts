import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import AuthCard from "../components/AuthCard";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [busy, setBusy] = useState(false);
  // Dev-only convenience: backend returns a link when DEBUG is on (no email
  // provider wired yet — Section 13.7).
  const [devLink, setDevLink] = useState(null);

  async function submit(e) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.requestPasswordReset(email);
      setDevLink(res.debug_reset_link || null);
      setSent(true);
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthCard
      title="Reset your password"
      subtitle="Enter your email and we'll send a reset link"
      footer={<Link to="/login">Back to sign in</Link>}
    >
      {sent ? (
        <div className="auth-form">
          <p className="success">
            If that email exists, a reset link is on its way.
          </p>
          {devLink && (
            <p className="muted dev-hint">
              Dev mode — open your reset link:{" "}
              <a href={devLink}>{devLink}</a>
            </p>
          )}
        </div>
      ) : (
        <form onSubmit={submit} className="auth-form">
          <label>
            Email
            <input type="email" value={email} required autoFocus
              onChange={(e) => setEmail(e.target.value)} />
          </label>
          <button type="submit" className="btn-primary btn-block" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </button>
        </form>
      )}
    </AuthCard>
  );
}
