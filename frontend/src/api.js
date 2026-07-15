// Thin REST client for the PulseCharts backend (Section 9).
// JWT access + refresh tokens live in localStorage; a 401 transparently tries a
// token refresh once and retries, so sessions survive past the access lifetime.

const ACCESS_KEY = "pulsecharts.access";
const REFRESH_KEY = "pulsecharts.refresh";

export const getToken = () => localStorage.getItem(ACCESS_KEY) || "";
const getRefresh = () => localStorage.getItem(REFRESH_KEY) || "";

export function setTokens({ access, refresh }) {
  if (access) localStorage.setItem(ACCESS_KEY, access);
  if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
}
export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY);
  localStorage.removeItem(REFRESH_KEY);
}

// The store registers a callback so it can flip isAuthed when refresh fails.
let onAuthExpired = () => {};
export function setOnAuthExpired(fn) {
  onAuthExpired = fn;
}

async function tryRefresh() {
  const refresh = getRefresh();
  if (!refresh) return false;
  const res = await fetch("/api/auth/token/refresh/", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  if (!res.ok) return false;
  const data = await res.json();
  setTokens({ access: data.access, refresh: data.refresh }); // refresh present if rotation on
  return true;
}

async function doFetch(path, { method, headers, body, auth }) {
  const h = { "Content-Type": "application/json", ...headers };
  const token = getToken();
  if (auth && token) h.Authorization = `Bearer ${token}`;
  return fetch(`/api${path}`, { method, headers: h, body: body ? JSON.stringify(body) : undefined });
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  let res = await doFetch(path, { method, body, auth });

  // One transparent refresh-and-retry on 401.
  if (res.status === 401 && auth && getRefresh()) {
    if (await tryRefresh()) {
      res = await doFetch(path, { method, body, auth });
    } else {
      clearTokens();
      onAuthExpired();
    }
  }

  if (!res.ok) {
    let detail = res.statusText;
    let parsed = null;
    try {
      const body = await res.json();
      parsed = body;
      // DRF errors come in several shapes: {detail}, ["msg"], {field: ["msg"]}.
      if (typeof body === "string") detail = body;
      else if (Array.isArray(body)) detail = body.join(" ");
      else if (body.detail) detail = Array.isArray(body.detail) ? body.detail.join(" ") : body.detail;
      else {
        const first = Object.values(body)[0];
        detail = Array.isArray(first) ? first.join(" ") : String(first ?? detail);
      }
    } catch {
      /* non-JSON error */
    }
    const err = new Error(detail);
    err.status = res.status;
    err.data = parsed;  // full error body, so callers can read custom fields (e.g. code)
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  // --- auth ---
  async login(email, password) {
    const tokens = await request("/auth/token/", { method: "POST", auth: false, body: { email, password } });
    setTokens(tokens);
    return tokens;
  },
  register: (email, password, referralCode) =>
    request("/auth/register/", {
      method: "POST",
      auth: false,
      body: { email, password, ...(referralCode ? { referral_code: referralCode } : {}) },
    }),
  verifyEmail: (uid, token) =>
    request("/auth/verify-email/", { method: "POST", auth: false, body: { uid, token } }),
  resendVerification: (email) =>
    request("/auth/verify-email/resend/", { method: "POST", auth: false, body: { email } }),
  requestPasswordReset: (email) =>
    request("/auth/password-reset/", { method: "POST", auth: false, body: { email } }),
  confirmPasswordReset: (uid, token, password) =>
    request("/auth/password-reset/confirm/", {
      method: "POST",
      auth: false,
      body: { uid, token, password },
    }),

  // --- market data (public endpoints, but send the token when present so the
  // backend can apply per-user plan gates like Pro-only symbols; anonymous
  // visitors still work — the header is only attached when a token exists) ---
  symbols: () => request("/symbols/", { auth: true }),
  candles: (ticker, interval = "1m", limit = 500) =>
    request(`/symbols/${ticker}/candles/?interval=${interval}&limit=${limit}`, { auth: true }),

  // --- user (auth) ---
  me: () => request("/me/"),
  changePassword: (oldPassword, newPassword) =>
    request("/me/change-password/", {
      method: "POST",
      body: { old_password: oldPassword, new_password: newPassword },
    }),
  checkout: (plan) => request("/billing/checkout/", { method: "POST", body: { plan } }),
  billingHistory: () => request("/billing/history/"),
  plans: () => request("/plans/", { auth: false }),
  entitlements: () => request("/me/entitlements/"),
  watchlist: () => request("/watchlist/"),
  addWatch: (symbolId) => request("/watchlist/", { method: "POST", body: { symbol_id: symbolId } }),
  removeWatch: (id) => request(`/watchlist/${id}/`, { method: "DELETE" }),

  // --- saved chart layouts (Section 9) ---
  layouts: () => request("/chart-layouts/"),
  saveLayout: (payload) => request("/chart-layouts/", { method: "POST", body: payload }),
  deleteLayout: (id) => request(`/chart-layouts/${id}/`, { method: "DELETE" }),

  // --- cross-device workspace sync ---
  getWorkspace: () => request("/me/workspace/"),
  saveWorkspace: (data) => request("/me/workspace/", { method: "PUT", body: { data } }),

  // --- trading signals (v2) ---
  signalServices: () => request("/signal-services/"),
  previewStrategy: (text) =>
    request("/signal-services/preview/", { method: "POST", body: { text } }),
  createStrategy: (text, name) =>
    request("/signal-services/", { method: "POST", body: { text, name } }),
  deleteStrategy: (id) =>
    request(`/signal-services/${id}/`, { method: "DELETE" }),
  signalSubscriptions: () => request("/me/signal-subscriptions/"),
  followService: (serviceId) =>
    request("/me/signal-subscriptions/", { method: "POST", body: { service_id: serviceId } }),
  unfollowService: (subId) =>
    request(`/me/signal-subscriptions/${subId}/`, { method: "DELETE" }),
  // offset pages the LIVE cards only; offset > 0 skips the resolved history.
  signalFeed: (offset = 0) =>
    request(`/me/signals/feed/${offset ? `?offset=${offset}` : ""}`),
  signalAccuracy: () => request("/signal-services/accuracy/"),

  // --- referrals (earn credits, redeem for a plan) ---
  referral: () => request("/me/referral/"),
  referralSetCode: (code) => request("/me/referral/code/", { method: "POST", body: { code } }),
  referralRedeem: (plan) => request("/me/referral/redeem/", { method: "POST", body: { plan } }),
  redeemPromoCode: (code) =>
    request("/me/referral/redeem-code/", { method: "POST", body: { code } }),

  // --- telegram signal delivery (premium) ---
  telegramStatus: () => request("/me/telegram/"),
  telegramDisconnect: () => request("/me/telegram/disconnect/", { method: "POST" }),
  telegramReconnect: () => request("/me/telegram/reconnect/", { method: "POST" }),

  // --- landing-page support chat (public, no LLM — curated knowledge base) ---
  supportSuggestions: () => request("/support/chat/", { auth: false }),
  supportChat: (message) =>
    request("/support/chat/", { method: "POST", auth: false, body: { message } }),
  supportContact: (email, message) =>
    request("/support/contact/", { method: "POST", auth: false, body: { email, message } }),

  // --- price alerts (v2) ---
  alerts: () => request("/me/alerts/"),
  createAlert: (symbolId, condition, targetPrice) =>
    request("/me/alerts/", {
      method: "POST",
      body: { symbol_id: symbolId, condition, target_price: targetPrice },
    }),
  deleteAlert: (id) => request(`/me/alerts/${id}/`, { method: "DELETE" }),
  markAlertsSeen: () => request("/me/alerts/seen/", { method: "POST" }),
  alertsUnseen: () => request("/me/alerts/unseen/"),
};
