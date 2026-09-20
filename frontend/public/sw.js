/* Service worker for signal push notifications.
 *
 * Deliberately minimal. It shows the notification and opens the feed — it never
 * renders trade levels, because a notification can sit unread for hours and a stale
 * entry price in a system tray is exactly the failure web push exists to avoid here.
 * The card in the app is always current.
 */

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (err) {
    // A malformed payload must still produce SOMETHING: a silent push is worse than
    // a generic one, since the user has no way to know a signal fired.
    data = {};
  }
  const title = data.title || "New signal";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "Open PulseCharts to view it.",
      // tag collapses repeats of the same signal into one notification rather than
      // stacking duplicates if a push is retried.
      tag: data.tag || "pulsecharts",
      data: { url: data.url || "/signals" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/signals";
  event.waitUntil(
    // Focus an existing tab if one is open rather than piling up new ones.
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
      for (const client of list) {
        if (client.url.includes(url) && "focus" in client) return client.focus();
      }
      return self.clients.openWindow(url);
    })
  );
});
