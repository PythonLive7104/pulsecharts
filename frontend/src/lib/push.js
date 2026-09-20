/**
 * Web Push subscription helpers.
 *
 * Why this exists: the signal feed is pull-based, so a signal was only delivered
 * when the user happened to open the page. Delivery latency measurably decides
 * whether a signal wins — the same call is worth materially more entered on its
 * trigger bar than hours later — so server-initiated push is what lets the feed use
 * the same tight freshness window as Telegram instead of trading accuracy away to
 * stay visible.
 *
 * Every function is defensive: push is unsupported on some browsers (notably iOS
 * Safari outside an installed PWA) and blockable everywhere, and none of that may
 * break the signals page.
 */

import { api } from "../api";

export function pushSupported() {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function pushPermission() {
  if (!pushSupported()) return "unsupported";
  return Notification.permission; // "granted" | "denied" | "default"
}

/** VAPID keys travel as URL-safe base64; PushManager wants a Uint8Array. */
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = window.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
  return out;
}

/** Subscribe this browser. Returns {ok, reason}. Never throws. */
export async function subscribePush() {
  if (!pushSupported()) return { ok: false, reason: "unsupported" };
  try {
    const cfg = await api.pushConfig();
    if (!cfg?.enabled || !cfg?.public_key) return { ok: false, reason: "disabled" };

    const permission = await Notification.requestPermission();
    if (permission !== "granted") return { ok: false, reason: permission };

    const reg = await navigator.serviceWorker.register("/sw.js");
    await navigator.serviceWorker.ready;

    // Reuse an existing subscription when there is one: re-subscribing returns the
    // same endpoint anyway, and calling subscribe() twice with different keys throws.
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true, // required by Chrome; we only send visible notifications
        applicationServerKey: urlBase64ToUint8Array(cfg.public_key),
      });
    }
    await api.pushSubscribe(sub.toJSON());
    return { ok: true, reason: "" };
  } catch (err) {
    return { ok: false, reason: "error" };
  }
}

/** Unsubscribe this browser, locally and server-side. Never throws. */
export async function unsubscribePush() {
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    const sub = reg && (await reg.pushManager.getSubscription());
    const endpoint = sub?.endpoint;
    if (sub) await sub.unsubscribe();
    // Tell the server even if the local unsubscribe failed — otherwise it keeps
    // pushing to an endpoint nobody is listening on.
    await api.pushUnsubscribe(endpoint ? { endpoint } : {});
    return { ok: true };
  } catch (err) {
    return { ok: false };
  }
}

/** Is this browser currently subscribed? */
export async function pushSubscribed() {
  if (!pushSupported()) return false;
  try {
    const reg = await navigator.serviceWorker.getRegistration();
    if (!reg) return false;
    return Boolean(await reg.pushManager.getSubscription());
  } catch (err) {
    return false;
  }
}
