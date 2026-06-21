// Profile & Settings — account info, change password, appearance.
import { useEffect, useState } from "react";
import { api } from "../../api";
import { useTheme } from "../../theme";

export default function ProfilePage() {
  const [me, setMe] = useState(null);
  const theme = useTheme((s) => s.theme);
  const toggle = useTheme((s) => s.toggle);

  useEffect(() => {
    api.me().then(setMe).catch(() => {});
  }, []);

  return (
    <div className="account-pages">
      <h1>Profile &amp; Settings</h1>

      <div className="card">
        <h2>Account</h2>
        <div className="field-row"><span className="field-label">Email</span><span>{me?.email || "…"}</span></div>
        <div className="field-row"><span className="field-label">Plan</span><span>{me?.plan_tier ? me.plan_tier.charAt(0).toUpperCase() + me.plan_tier.slice(1) : "Free"}</span></div>
      </div>

      <div className="card">
        <h2>Appearance</h2>
        <div className="field-row">
          <span className="field-label">Theme</span>
          <button className="btn-ghost" onClick={toggle}>
            {theme === "dark" ? "🌙 Dark" : "☀️ Light"} — switch
          </button>
        </div>
      </div>

      <ChangePassword />
    </div>
  );
}

function ChangePassword() {
  const [oldPassword, setOld] = useState("");
  const [newPassword, setNew] = useState("");
  const [confirm, setConfirm] = useState("");
  const [msg, setMsg] = useState(null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setMsg(null);
    setErr(null);
    if (newPassword !== confirm) return setErr("New passwords don't match.");
    if (newPassword.length < 8) return setErr("New password must be at least 8 characters.");
    setBusy(true);
    try {
      await api.changePassword(oldPassword, newPassword);
      setMsg("Password updated.");
      setOld(""); setNew(""); setConfirm("");
    } catch (e2) {
      setErr(e2.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h2>Change password</h2>
      <form onSubmit={submit} className="auth-form">
        <label>Current password
          <input type="password" value={oldPassword} onChange={(e) => setOld(e.target.value)} required />
        </label>
        <label>New password
          <input type="password" value={newPassword} onChange={(e) => setNew(e.target.value)} required />
        </label>
        <label>Confirm new password
          <input type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} required />
        </label>
        {err && <p className="error">{err}</p>}
        {msg && <p className="success">{msg}</p>}
        <button className="btn-primary" disabled={busy}>{busy ? "Saving…" : "Update password"}</button>
      </form>
    </div>
  );
}
