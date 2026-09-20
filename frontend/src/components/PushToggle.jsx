import { useEffect, useState } from "react";
import {
  pushPermission,
  pushSubscribed,
  pushSupported,
  subscribePush,
  unsubscribePush,
} from "../lib/push";
import { api } from "../api";

/**
 * Enable/disable browser notifications for new signals.
 *
 * This is a delivery-SPEED control, not a convenience one. The feed is pull-based,
 * so without push a signal only reaches a user when they next open the page — and
 * delivery latency measurably decides whether a signal wins. The copy says that
 * plainly rather than calling it "alerts", because a user who leaves it off is
 * choosing a materially different (and worse) version of the product.
 *
 * Renders nothing when the server has no VAPID keys, so an unconfigured deployment
 * never shows a control that cannot work.
 */
export default function PushToggle() {
  const [enabled, setEnabled] = useState(null); // null = still checking
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const cfg = await api.pushConfig();
        if (!alive) return;
        setEnabled(Boolean(cfg?.enabled) && pushSupported());
        setOn(await pushSubscribed());
      } catch {
        if (alive) setEnabled(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  if (enabled === null || enabled === false) return null;

  const denied = pushPermission() === "denied";

  async function toggle() {
    setBusy(true);
    setMsg("");
    if (on) {
      await unsubscribePush();
      setOn(false);
    } else {
      const { ok, reason } = await subscribePush();
      setOn(ok);
      if (!ok) {
        setMsg(
          reason === "denied"
            ? "Notifications are blocked for this site in your browser settings."
            : "Couldn't enable notifications on this device."
        );
      }
    }
    setBusy(false);
  }

  return (
    <div className="push-toggle">
      <div className="push-copy">
        <b>Signal notifications</b>
        <span className="muted">
          {on
            ? "On — new signals are pushed to this device as they fire."
            : "Off — you only see new signals when you open this page."}
        </span>
      </div>
      <button type="button" onClick={toggle} disabled={busy || denied}>
        {busy ? "…" : on ? "Turn off" : "Turn on"}
      </button>
      {(msg || denied) && (
        <span className="push-msg">
          {msg ||
            "Notifications are blocked for this site in your browser settings."}
        </span>
      )}
    </div>
  );
}
