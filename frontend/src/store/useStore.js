// Central app state (Section 3 — Zustand). Multi-chart: the workspace is a list
// of independent panes (each with its own symbol/timeframe/candles/indicators/
// params). Sidebar controls act on the *active* pane.
import { create } from "zustand";
import { api, getToken, clearTokens, setOnAuthExpired } from "../api";

export const TIMEFRAMES = ["1m", "5m", "15m", "1h", "4h", "1d"];
export const LAYOUTS = { "1": 1, "2": 2, "4": 4 };

let paneSeq = 0;
const newPaneId = () => `pane-${++paneSeq}`;
const makePane = (over = {}) => ({
  id: newPaneId(),
  symbol: null,
  timeframe: "1m",
  candles: [],
  indicators: new Set(["volume"]),
  params: {},
  drawings: [], // drawing-tool objects { id, tool, color, points }
  status: "idle",
  error: null,
  ...over,
});

// --- workspace persistence (localStorage) ---
// Survives a refresh: panes (symbol/timeframe/indicators/params/drawings) + the
// layout. Candles are NOT persisted — they're re-fetched on load.
const WORKSPACE_KEY = "pulsecharts.workspace";

function snapshot(state) {
  return {
    layout: state.layout,
    activePaneId: state.activePaneId,
    drawColor: state.drawColor,
    panes: state.panes.map((p) => ({
      id: p.id,
      symbol: p.symbol,
      timeframe: p.timeframe,
      indicators: [...p.indicators],
      params: p.params,
      drawings: p.drawings,
    })),
  };
}

function hydrateSnapshot(w) {
  if (!w || !Array.isArray(w.panes) || w.panes.length === 0) return null;
  // Keep the pane-id counter ahead of any restored ids to avoid collisions.
  let maxSeq = 0;
  const panes = w.panes.map((p) => {
    const m = /pane-(\d+)/.exec(p.id || "");
    if (m) maxSeq = Math.max(maxSeq, Number(m[1]));
    return makePane({
      id: p.id,
      symbol: p.symbol || null,
      timeframe: p.timeframe || "1m",
      indicators: new Set(p.indicators?.length ? p.indicators : ["volume"]),
      params: p.params || {},
      drawings: Array.isArray(p.drawings) ? p.drawings : [],
    });
  });
  paneSeq = Math.max(paneSeq, maxSeq);
  return { layout: w.layout || "1", activePaneId: w.activePaneId || panes[0].id, drawColor: w.drawColor, panes };
}

function loadWorkspace() {
  try {
    return hydrateSnapshot(JSON.parse(localStorage.getItem(WORKSPACE_KEY) || "null"));
  } catch {
    return null;
  }
}

// Server sync only after the initial fetch, so an early local change can't
// clobber the server copy before we've read it.
let _serverSyncReady = false;
const _persisted = loadWorkspace();

export const useStore = create((set, get) => ({
  // --- auth / entitlements ---
  isAuthed: Boolean(getToken()),
  entitlements: null,

  // --- workspace ---
  symbols: [],
  // UI filter for the symbol search/picker: "crypto" | "forex". Transient.
  assetClass: "crypto",
  layout: _persisted?.layout || "1",
  panes: _persisted?.panes || [makePane()],
  activePaneId: _persisted?.activePaneId || null,

  // --- drawing tools ---
  activeTool: "cursor", // cursor | select | trendline | ray | horizontal | vertical | fib | channel | elliott
  drawColor: _persisted?.drawColor || "#4285f4",
  selected: null, // { paneId, id } of the selected drawing

  // --- helpers ---
  paneById: (id) => get().panes.find((p) => p.id === id),
  activePane: () => get().paneById(get().activePaneId) || get().panes[0],
  _patchPane(id, patch) {
    set({ panes: get().panes.map((p) => (p.id === id ? { ...p, ...patch } : p)) });
  },

  // --- auth actions ---
  async login(email, password) {
    await api.login(email, password);
    set({ isAuthed: true });
    await get().loadEntitlements();
  },
  async register(email, password, referralCode) {
    await api.register(email, password, referralCode);
    await get().login(email, password);
  },
  logout() {
    clearTokens();
    // Clear the local workspace cache + reset panes so the next user who signs
    // in on this browser never sees the previous user's charts.
    try {
      localStorage.removeItem(WORKSPACE_KEY);
    } catch {
      /* ignore */
    }
    const pane = makePane();
    set({
      isAuthed: false,
      entitlements: null,
      layout: "1",
      panes: [pane],
      activePaneId: pane.id,
      selected: null,
    });
  },
  async loadEntitlements() {
    if (!getToken()) return;
    try {
      set({ entitlements: await api.entitlements() });
    } catch {
      get().logout();
    }
  },

  // --- workspace actions ---
  // Boot the workspace: pull the user's server-synced workspace (cross-device),
  // hydrate from it if present, then load symbols + candles. Falls back to the
  // localStorage copy already loaded at init if the server has nothing/offline.
  async initWorkspace() {
    if (getToken()) {
      try {
        const ws = await api.getWorkspace();
        const hydrated = hydrateSnapshot(ws?.data);
        if (hydrated) {
          set({
            layout: hydrated.layout,
            activePaneId: hydrated.activePaneId,
            drawColor: hydrated.drawColor || get().drawColor,
            panes: hydrated.panes,
            selected: null,
          });
        }
      } catch {
        /* offline / unauthorized — keep the local copy */
      }
    }
    _serverSyncReady = true;
    await get().loadSymbols();
  },

  async loadSymbols() {
    const symbols = await api.symbols();
    set({ symbols });
    if (!get().activePaneId) set({ activePaneId: get().panes[0].id });
    if (symbols.length) {
      for (const p of get().panes) {
        if (!p.symbol) {
          await get().selectSymbol(p.id, symbols[0].ticker); // seed empty pane
        } else if (p.candles.length === 0) {
          await get().loadCandlesFor(p.id); // fetch candles for a restored pane
        }
      }
      get()._syncAssetClass(); // toggle should match the restored active symbol
    }
  },

  setActivePane(id) {
    if (!get().paneById(id)) return;
    set({ activePaneId: id });
    get()._syncAssetClass();
  },

  // Keep the Crypto/Forex toggle in step with the symbol the active pane is
  // actually showing (e.g. after a reload restores a forex chart).
  _syncAssetClass() {
    const pane = get().activePane();
    const sym = get().symbols.find((s) => s.ticker === pane?.symbol);
    if (sym?.asset_class && sym.asset_class !== get().assetClass) {
      set({ assetClass: sym.asset_class });
    }
  },

  setLayout(layout) {
    const n = LAYOUTS[layout] || 1;
    let panes = [...get().panes];
    while (panes.length < n) {
      const seed = panes[panes.length - 1];
      panes.push(makePane({ symbol: seed?.symbol || null, timeframe: seed?.timeframe || "1m" }));
    }
    if (panes.length > n) panes = panes.slice(0, n);
    const activePaneId = panes.some((p) => p.id === get().activePaneId)
      ? get().activePaneId
      : panes[0].id;
    set({ layout, panes, activePaneId });
    // Load candles for any freshly-added pane that has a symbol but no data.
    for (const p of panes) if (p.symbol && p.candles.length === 0) get().loadCandlesFor(p.id);
  },

  // Switch the symbol picker between crypto and forex. If the active pane is
  // showing the other asset class, jump it to the first symbol of the new class
  // so the toggle has an immediate effect.
  setAssetClass(cls) {
    if (cls === get().assetClass) return;
    set({ assetClass: cls });
    const { symbols, activePane, selectSymbol } = get();
    const pane = activePane();
    if (!pane) return;
    const current = symbols.find((s) => s.ticker === pane.symbol);
    if (!current || current.asset_class !== cls) {
      const first = symbols.find((s) => s.asset_class === cls);
      if (first) selectSymbol(pane.id, first.ticker);
    }
  },

  async selectSymbol(paneId, ticker) {
    const pane = get().paneById(paneId);
    if (!pane || ticker === pane.symbol) return;
    get()._patchPane(paneId, { symbol: ticker, status: "loading", error: null, candles: [] });
    await get().loadCandlesFor(paneId);
  },

  async setTimeframe(paneId, tf) {
    const pane = get().paneById(paneId);
    if (!pane || tf === pane.timeframe) return;
    get()._patchPane(paneId, { timeframe: tf });
    await get().loadCandlesFor(paneId);
  },

  async loadCandlesFor(paneId) {
    const pane = get().paneById(paneId);
    if (!pane || !pane.symbol) return;
    get()._patchPane(paneId, { status: "loading" });
    try {
      const { candles } = await api.candles(pane.symbol, pane.timeframe);
      get()._patchPane(paneId, { candles, status: "live" });
    } catch (e) {
      get()._patchPane(paneId, { status: "error", error: e.message });
    }
  },

  // Merge a live candle into every pane showing that symbol at that interval.
  // The symbol+interval match prevents cross-contamination between panes and
  // ensures a 1m tick never lands on a 5m/1h chart.
  applyLiveCandle(candle) {
    set({
      panes: get().panes.map((p) => {
        if (p.symbol !== candle.symbol || p.timeframe !== candle.interval) return p;
        const last = p.candles[p.candles.length - 1];
        if (last && candle.time === last.time) {
          return { ...p, candles: [...p.candles.slice(0, -1), candle] };
        }
        if (!last || candle.time > last.time) {
          return { ...p, candles: [...p.candles, candle] };
        }
        return p;
      }),
    });
  },

  toggleIndicator(paneId, slug) {
    const pane = get().paneById(paneId);
    if (!pane) return;
    const next = new Set(pane.indicators);
    next.has(slug) ? next.delete(slug) : next.add(slug);
    get()._patchPane(paneId, { indicators: next });
  },

  setIndicatorParam(paneId, slug, key, value) {
    const pane = get().paneById(paneId);
    if (!pane) return;
    get()._patchPane(paneId, {
      params: { ...pane.params, [slug]: { ...pane.params[slug], [key]: value } },
    });
  },

  // --- saved layouts (act on the active pane) ---
  async applyLayout({ ticker, timeframe, active, params, drawings }) {
    const pane = get().activePane();
    if (!pane) return;
    get()._patchPane(pane.id, {
      timeframe: timeframe || pane.timeframe,
      indicators: new Set(active || ["volume"]),
      params: params || {},
      drawings: Array.isArray(drawings) ? drawings : [],
      symbol: ticker || pane.symbol,
      candles: [],
      status: "loading",
    });
    await get().loadCandlesFor(pane.id);
  },

  currentLayoutConfig() {
    const p = get().activePane();
    return { active: [...p.indicators], params: p.params, drawings: p.drawings };
  },

  // --- drawing-tool actions ---
  setTool(tool) {
    set({ activeTool: tool });
  },
  setDrawColor(drawColor) {
    set({ drawColor });
  },
  addDrawing(paneId, drawing) {
    const pane = get().paneById(paneId);
    if (!pane) return;
    get()._patchPane(paneId, { drawings: [...pane.drawings, drawing] });
  },
  undoDrawing(paneId) {
    const pane = get().paneById(paneId);
    if (!pane || pane.drawings.length === 0) return;
    get()._patchPane(paneId, { drawings: pane.drawings.slice(0, -1) });
  },
  clearDrawings(paneId) {
    get()._patchPane(paneId, { drawings: [] });
    set({ selected: null });
  },
  selectDrawing(paneId, id) {
    set({ selected: id ? { paneId, id } : null });
  },
  updateDrawing(paneId, id, points) {
    const pane = get().paneById(paneId);
    if (!pane) return;
    get()._patchPane(paneId, {
      drawings: pane.drawings.map((d) => (d.id === id ? { ...d, points } : d)),
    });
  },
  deleteDrawing(paneId, id) {
    const pane = get().paneById(paneId);
    if (!pane) return;
    get()._patchPane(paneId, { drawings: pane.drawings.filter((d) => d.id !== id) });
    const sel = get().selected;
    if (sel && sel.paneId === paneId && sel.id === id) set({ selected: null });
  },
}));

// When a token refresh fails, drop auth state so the UI reflects it.
setOnAuthExpired(() => useStore.getState().logout());

// Persist the workspace on change (debounced so live candle ticks don't thrash
// storage — the snapshot omits candles anyway). localStorage is the fast local
// cache; the server copy syncs it across devices (once initWorkspace has run).
let _saveTimer = null;
let _serverTimer = null;
useStore.subscribe((state) => {
  const snap = snapshot(state);
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => {
    try {
      localStorage.setItem(WORKSPACE_KEY, JSON.stringify(snap));
    } catch {
      /* quota / serialization — ignore */
    }
  }, 600);
  if (getToken() && _serverSyncReady) {
    clearTimeout(_serverTimer);
    _serverTimer = setTimeout(() => {
      api.saveWorkspace(snap).catch(() => {
        /* offline — localStorage still holds it; next change retries */
      });
    }, 2000);
  }
});
