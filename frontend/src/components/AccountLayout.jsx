// Account area shell — sidebar nav + routed sub-pages (profile, billing).
// Separate from the charting dashboard (/app); reachable from the topbar.
import { Link, NavLink, Outlet } from "react-router-dom";
import { useStore } from "../store/useStore";
import ThemeToggle from "./ThemeToggle";
import Logo from "./Logo";

export default function AccountLayout() {
  const entitlements = useStore((s) => s.entitlements);
  const logout = useStore((s) => s.logout);

  return (
    <div className="account">
      <header className="topbar">
        <Link to="/" className="brand"><Logo /></Link>
        <div className="topbar-right">
          <Link to="/app" className="btn-ghost">← Back to charts</Link>
          <ThemeToggle />
          <span className="plan-pill">{entitlements?.is_premium ? "★ " : ""}{entitlements?.plan_label || "Free"}</span>
          <button className="btn-ghost" onClick={logout}>Sign out</button>
        </div>
      </header>

      <div className="account-body">
        <nav className="account-nav">
          <h3>Account</h3>
          <NavLink to="/account/profile" className={({ isActive }) => (isActive ? "active" : "")}>
            Profile &amp; Settings
          </NavLink>
          <NavLink to="/account/billing" className={({ isActive }) => (isActive ? "active" : "")}>
            Plan &amp; Billing
          </NavLink>
        </nav>
        <section className="account-content">
          <Outlet />
        </section>
      </div>
    </div>
  );
}
