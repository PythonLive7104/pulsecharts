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
    try {
      const body = await res.json();
      // DRF errors come in several shapes: {detail}, ["msg"], {field: ["msg"]}.
      if (typeof body === "string") detail = body;
      else if (Array.isArray(body)) detail = body.join(" ");
      else if (body.detail) detail = body.detail;
      else {
        const first = Object.values(body)[0];
        detail = Array.isArray(first) ? first.join(" ") : String(first ?? detail);
      }
    } catch {
      /* non-JSON error */
    }
    const err = new Error(detail);
    err.status = res.status;
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
  requestPasswordReset: (email) =>
    request("/auth/password-reset/", { method: "POST", auth: false, body: { email } }),
  confirmPasswordReset: (uid, token, password) =>
    request("/auth/password-reset/confirm/", {
      method: "POST",
      auth: false,
      body: { uid, token, password },
    }),

  // --- market data (public) ---
  symbols: () => request("/symbols/", { auth: false }),
  candles: (ticker, interval = "1m", limit = 500) =>
    request(`/symbols/${ticker}/candles/?interval=${interval}&limit=${limit}`, { auth: false }),

  // --- user (auth) ---
  me: () => request("/me/"),
  changePassword: (oldPassword, newPassword) =>
    request("/me/change-password/", {
      method: "POST",
      body: { old_password: oldPassword, new_password: newPassword },
    }),
  checkout: (plan) => request("/billing/checkout/", { method: "POST", body: { plan } }),
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
  signalSubscriptions: () => request("/me/signal-subscriptions/"),
  followService: (serviceId) =>
    request("/me/signal-subscriptions/", { method: "POST", body: { service_id: serviceId } }),
  unfollowService: (subId) =>
    request(`/me/signal-subscriptions/${subId}/`, { method: "DELETE" }),
  signalFeed: () => request("/me/signals/feed/"),
  signalAccuracy: () => request("/signal-services/accuracy/"),

  // --- referrals (earn credits, redeem for a plan) ---
  referral: () => request("/me/referral/"),
  referralSetCode: (code) => request("/me/referral/code/", { method: "POST", body: { code } }),
  referralRedeem: (plan) => request("/me/referral/redeem/", { method: "POST", body: { plan } }),

  // --- telegram signal delivery (premium) ---
  telegramStatus: () => request("/me/telegram/"),
  telegramDisconnect: () => request("/me/telegram/disconnect/", { method: "POST" }),

  // --- auto-trade / broker execution (Pro, v2) ---
  brokerStatus: () => request("/me/broker/"),
  brokerConnect: (apiKey, apiSecret, testnet, authorize) =>
    request("/me/broker/", {
      method: "POST",
      body: { api_key: apiKey, api_secret: apiSecret, testnet, authorize },
    }),
  brokerDisconnect: () => request("/me/broker/", { method: "DELETE" }),
  autoTradeConfig: () => request("/me/auto-trade/config/"),
  saveAutoTradeConfig: (payload) =>
    request("/me/auto-trade/config/", { method: "PUT", body: payload }),
  autoTradeExecutions: () => request("/me/auto-trade/executions/"),
  autoTradePanic: () => request("/me/auto-trade/panic/", { method: "POST" }),

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
