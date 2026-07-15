// Email-verification landing page. The verification email links here with
// ?uid=…&token=…; we POST them to the backend and show the result. On success the
// user can sign in (their account is now verified). On an expired/invalid link we
// offer to resend.
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api } from "../api";
import AuthCard from "../components/AuthCard";

export default function VerifyEmailPage() {
  const [params] = useSearchParams();
  const uid = params.get("uid");
  const token = params.get("token");
  const [state, setState] = useState("checking"); // checking | ok | error
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [resendMsg, setResendMsg] = useState(null);
  // StrictMode double-invokes effects in dev; guard so we don't POST twice.
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    if (!uid || !token) {
      setState("error");
      setMessage("This verification link is incomplete. Use the button in your email.");
      return;
    }
    api.verifyEmail(uid, token)
      .then((res) => {
        setState("ok");
        setMessage(res?.detail || "Your email is verified.");
      })
      .catch((err) => {
        setState("error");
        setMessage(err.message || "This verification link is invalid or has expired.");
      });
  }, [uid, token]);

  async function resend() {
    setResendMsg(null);
    try {
      await api.resendVerification(email);
      setResendMsg("If that account still needs verifying, a fresh link is on its way.");
    } catch {
      setResendMsg("Couldn't resend just now. Please try again shortly.");
    }
  }

  return (
    <AuthCard
      title={state === "ok" ? "Email verified ✓" : "Verify your email"}
      subtitle={state === "ok" ? "You're all set" : "Confirming your account"}
      footer={<span>Back to <Link to="/login">Sign in</Link></span>}
    >
      {state === "checking" && <p className="muted">Verifying your link…</p>}

      {state === "ok" && (
        <div className="verify-sent">
          <p>{message}</p>
          <Link to="/login" className="btn-primary btn-block">Sign in</Link>
        </div>
      )}

      {state === "error" && (
        <div className="verify-sent">
          <p className="error">{message}</p>
          <p className="muted">Enter your email to get a new verification link:</p>
          <input
            type="email"
            className="verify-email-input"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <button type="button" className="btn-ghost btn-block" onClick={resend} disabled={!email}>
            Resend verification email
          </button>
          {resendMsg && <p className="auth-hint">{resendMsg}</p>}
        </div>
      )}
    </AuthCard>
  );
}
