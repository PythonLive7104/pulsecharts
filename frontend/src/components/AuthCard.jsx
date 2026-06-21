// Shared shell for the auth pages (login / signup / forgot / reset).
// Split layout: a branded benefits panel (hidden on small screens) beside the
// form card.
import { Link } from "react-router-dom";
import ThemeToggle from "./ThemeToggle";
import Logo from "./Logo";

const BENEFITS = [
  "Real-time candles for Hyperliquid coins",
  "SMA, EMA & Volume — free forever",
  "Premium indicators & saved layouts",
  "Algorithmic trading-signal feed",
];

export default function AuthCard({ title, subtitle, children, footer }) {
  return (
    <div className="auth-page">
      <div className="auth-split">
        <aside className="auth-aside">
          <Link to="/" className="brand"><Logo size={26} /></Link>
          <h2 className="auth-aside-head">
            Pro-grade crypto charts,<br />without the pro-grade price.
          </h2>
          <ul className="auth-benefits">
            {BENEFITS.map((b) => (
              <li key={b}><span className="auth-check">✓</span>{b}</li>
            ))}
          </ul>
          <p className="auth-aside-foot">⚡ Powered by Hyperliquid market data</p>
        </aside>

        <div className="auth-main">
          <div className="auth-main-top">
            <Link to="/" className="brand auth-brand-mobile"><Logo /></Link>
            <ThemeToggle />
          </div>
          <div className="auth-card">
            <h1>{title}</h1>
            {subtitle && <p className="auth-sub">{subtitle}</p>}
            {children}
            {footer && <div className="auth-footer">{footer}</div>}
          </div>
        </div>
      </div>
    </div>
  );
}
