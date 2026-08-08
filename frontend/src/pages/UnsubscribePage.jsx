// Marketing-email opt-out. Deliberately public and login-free: making someone sign in
// to stop promotional email is how you get reported as spam instead of unsubscribed.
import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import Logo from "../components/Logo";

export default function UnsubscribePage() {
  const { token } = useParams();
  const [state, setState] = useState({ status: "working" });

  useEffect(() => {
    api.unsubscribe(token)
      .then((r) => setState({ status: "done", email: r.email }))
      .catch((e) => setState({ status: "error", message: e.message }));
  }, [token]);

  return (
    <div className="auth-page">
      <div className="auth-card">
        <span className="brand"><Logo /></span>
        {state.status === "working" && <p className="muted">Unsubscribing…</p>}
        {state.status === "done" && (
          <>
            <h1>You're unsubscribed</h1>
            <p className="muted">
              {state.email ? <><b>{state.email}</b> won't </> : "You won't "}
              receive marketing emails from us again.
            </p>
            <p className="muted">
              You'll still get essential account email — payment receipts, password
              resets and signal alerts you've asked for.
            </p>
            <Link to="/app" className="btn-primary btn-block">Back to PulseCharts</Link>
          </>
        )}
        {state.status === "error" && (
          <>
            <h1>That link didn't work</h1>
            <p className="error">{state.message}</p>
            <p className="muted">
              Email us and we'll remove you by hand — you don't have to use the link.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
