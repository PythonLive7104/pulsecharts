// Client-side indicator math (Section 10 — all indicators computed in the
// browser against the candle buffer, no backend math at MVP).
//
// Each indicator exposes a `compute(candles, params)` that returns an array of
// "series specs" the chart renders:
//   { id, type: "line" | "histogram", color, data: [{ time, value }], lineWidth? }
// Registry entries declare a `pane`: "price" (overlay on the candles) or "lower"
// (a separate oscillator band). Premium indicators are gated by entitlements
// (Section 11) — this file just does the math.
//
// Candle shape: { time, open, high, low, close, volume }.

// ---------------------------------------------------------------- primitives

const mean = (arr) => arr.reduce((a, b) => a + b, 0) / arr.length;

// SMA over a numeric array; out[i] is null until enough data (warmup).
function smaArr(values, period) {
  const out = new Array(values.length).fill(null);
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= period) sum -= values[i - period];
    if (i >= period - 1) out[i] = sum / period;
  }
  return out;
}

// EMA over a numeric array, seeded with the SMA of the first `period` values.
function emaArr(values, period) {
  const out = new Array(values.length).fill(null);
  if (values.length < period) return out;
  const k = 2 / (period + 1);
  let prev = mean(values.slice(0, period));
  out[period - 1] = prev;
  for (let i = period; i < values.length; i++) {
    prev = values[i] * k + prev * (1 - k);
    out[i] = prev;
  }
  return out;
}

// Rolling population standard deviation; out[i] null during warmup.
function stdArr(values, period) {
  const out = new Array(values.length).fill(null);
  for (let i = period - 1; i < values.length; i++) {
    const window = values.slice(i - period + 1, i + 1);
    const m = mean(window);
    out[i] = Math.sqrt(mean(window.map((v) => (v - m) ** 2)));
  }
  return out;
}

const highestHigh = (candles, i, period) =>
  Math.max(...candles.slice(i - period + 1, i + 1).map((c) => c.high));
const lowestLow = (candles, i, period) =>
  Math.min(...candles.slice(i - period + 1, i + 1).map((c) => c.low));

// Pair an aligned value array back with candle times, dropping nulls.
function toLine(candles, values) {
  const data = [];
  for (let i = 0; i < candles.length; i++) {
    if (values[i] != null && Number.isFinite(values[i])) {
      data.push({ time: candles[i].time, value: values[i] });
    }
  }
  return data;
}

const closes = (candles) => candles.map((c) => c.close);

// ---------------------------------------------------------------- indicators

export function sma(candles, { period = 20 } = {}) {
  return [{ id: "sma", type: "line", color: "#f4b400", data: toLine(candles, smaArr(closes(candles), period)) }];
}

export function ema(candles, { period = 21 } = {}) {
  return [{ id: "ema", type: "line", color: "#4285f4", data: toLine(candles, emaArr(closes(candles), period)) }];
}

// RSI — Wilder's smoothing. 0–100.
export function rsi(candles, { period = 14 } = {}) {
  const c = closes(candles);
  const out = new Array(c.length).fill(null);
  if (c.length > period) {
    let gain = 0, loss = 0;
    for (let i = 1; i <= period; i++) {
      const d = c[i] - c[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    gain /= period; loss /= period;
    const rsiVal = () => (loss === 0 ? 100 : 100 - 100 / (1 + gain / loss));
    out[period] = rsiVal();
    for (let i = period + 1; i < c.length; i++) {
      const d = c[i] - c[i - 1];
      gain = (gain * (period - 1) + (d > 0 ? d : 0)) / period;
      loss = (loss * (period - 1) + (d < 0 ? -d : 0)) / period;
      out[i] = rsiVal();
    }
  }
  return [{ id: "rsi", type: "line", color: "#a142f4", data: toLine(candles, out) }];
}

// MACD — fast/slow EMA difference, signal EMA of that, plus histogram.
export function macd(candles, { fast = 12, slow = 26, signalPeriod = 9 } = {}) {
  const c = closes(candles);
  const ef = emaArr(c, fast);
  const es = emaArr(c, slow);
  const macdArr = c.map((_, i) => (ef[i] != null && es[i] != null ? ef[i] - es[i] : null));

  // Signal = EMA of the macd line over its non-null tail, mapped back by index.
  const idx = macdArr.map((v, i) => (v != null ? i : -1)).filter((i) => i >= 0);
  const sigVals = emaArr(idx.map((i) => macdArr[i]), signalPeriod);
  const signalArr = new Array(c.length).fill(null);
  idx.forEach((i, k) => { signalArr[i] = sigVals[k]; });

  const histArr = macdArr.map((v, i) => (v != null && signalArr[i] != null ? v - signalArr[i] : null));
  const histData = [];
  for (let i = 0; i < candles.length; i++) {
    if (histArr[i] != null) {
      histData.push({
        time: candles[i].time,
        value: histArr[i],
        color: histArr[i] >= 0 ? "#26a69a88" : "#ef535088",
      });
    }
  }
  return [
    { id: "hist", type: "histogram", color: "#888", data: histData },
    { id: "macd", type: "line", color: "#2196f3", data: toLine(candles, macdArr) },
    { id: "signal", type: "line", color: "#ff9800", data: toLine(candles, signalArr) },
  ];
}

// Bollinger Bands — SMA middle with ±mult·stddev bands (price overlay).
export function bbands(candles, { period = 20, mult = 2 } = {}) {
  const c = closes(candles);
  const mid = smaArr(c, period);
  const sd = stdArr(c, period);
  const upper = mid.map((m, i) => (m != null && sd[i] != null ? m + mult * sd[i] : null));
  const lower = mid.map((m, i) => (m != null && sd[i] != null ? m - mult * sd[i] : null));
  return [
    { id: "upper", type: "line", color: "#26c6da", lineWidth: 1, data: toLine(candles, upper) },
    { id: "mid", type: "line", color: "#26c6da", lineWidth: 1, data: toLine(candles, mid) },
    { id: "lower", type: "line", color: "#26c6da", lineWidth: 1, data: toLine(candles, lower) },
  ];
}

// Stochastic oscillator — %K and its %D (SMA of %K). 0–100.
export function stoch(candles, { kPeriod = 14, dPeriod = 3 } = {}) {
  const kArr = new Array(candles.length).fill(null);
  for (let i = kPeriod - 1; i < candles.length; i++) {
    const hh = highestHigh(candles, i, kPeriod);
    const ll = lowestLow(candles, i, kPeriod);
    kArr[i] = hh === ll ? 100 : (100 * (candles[i].close - ll)) / (hh - ll);
  }
  const kClean = kArr.map((v) => (v == null ? NaN : v));
  const dArr = smaArr(kClean, dPeriod).map((v) => (Number.isFinite(v) ? v : null));
  return [
    { id: "k", type: "line", color: "#42a5f5", data: toLine(candles, kArr) },
    { id: "d", type: "line", color: "#ef5350", data: toLine(candles, dArr) },
  ];
}

// ATR — Wilder-smoothed Average True Range (lower pane, price-unit volatility).
export function atr(candles, { period = 14 } = {}) {
  const tr = new Array(candles.length).fill(null);
  for (let i = 1; i < candles.length; i++) {
    const h = candles[i].high, l = candles[i].low, pc = candles[i - 1].close;
    tr[i] = Math.max(h - l, Math.abs(h - pc), Math.abs(l - pc));
  }
  const out = new Array(candles.length).fill(null);
  if (candles.length > period) {
    let prev = mean(tr.slice(1, period + 1));
    out[period] = prev;
    for (let i = period + 1; i < candles.length; i++) {
      prev = (prev * (period - 1) + tr[i]) / period;
      out[i] = prev;
    }
  }
  return [{ id: "atr", type: "line", color: "#ab47bc", data: toLine(candles, out) }];
}

// VWAP — volume-weighted average price, reset each UTC day (session anchor).
export function vwap(candles) {
  const out = [];
  let day = null, cumPV = 0, cumV = 0;
  for (const c of candles) {
    const d = Math.floor(c.time / 86400);
    if (d !== day) { day = d; cumPV = 0; cumV = 0; }
    const typical = (c.high + c.low + c.close) / 3;
    cumPV += typical * c.volume;
    cumV += c.volume;
    if (cumV > 0) out.push({ time: c.time, value: cumPV / cumV });
  }
  return [{ id: "vwap", type: "line", color: "#ffa726", data: out }];
}

// Fibonacci retracement — horizontal levels between buffer high and low.
const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
export function fib(candles) {
  if (candles.length < 2) return [];
  const hi = Math.max(...candles.map((c) => c.high));
  const lo = Math.min(...candles.map((c) => c.low));
  const t0 = candles[0].time;
  const t1 = candles[candles.length - 1].time;
  return FIB_LEVELS.map((level) => {
    const price = hi - (hi - lo) * level;
    return {
      id: `fib-${level}`,
      type: "line",
      color: "#9e9e9e",
      lineWidth: 1,
      data: [{ time: t0, value: price }, { time: t1, value: price }],
    };
  });
}

// Ichimoku Cloud — Tenkan/Kijun/Senkou A/Senkou B + Chikou (price overlay).
// Note: standard forward displacement of the Senkou span (cloud) by 26 needs
// future timestamps the chart doesn't have, so spans are drawn at the current
// bar and Chikou is shifted back; the filled cloud is a later polish.
export function ichimoku(candles, { conv = 9, base = 26, spanB = 52 } = {}) {
  const tenkan = new Array(candles.length).fill(null);
  const kijun = new Array(candles.length).fill(null);
  const senkouA = new Array(candles.length).fill(null);
  const senkouB = new Array(candles.length).fill(null);
  for (let i = 0; i < candles.length; i++) {
    if (i >= conv - 1)
      tenkan[i] = (highestHigh(candles, i, conv) + lowestLow(candles, i, conv)) / 2;
    if (i >= base - 1)
      kijun[i] = (highestHigh(candles, i, base) + lowestLow(candles, i, base)) / 2;
    if (tenkan[i] != null && kijun[i] != null)
      senkouA[i] = (tenkan[i] + kijun[i]) / 2;
    if (i >= spanB - 1)
      senkouB[i] = (highestHigh(candles, i, spanB) + lowestLow(candles, i, spanB)) / 2;
  }
  // Chikou: today's close plotted `base` bars back.
  const chikou = [];
  for (let i = base; i < candles.length; i++) {
    chikou.push({ time: candles[i - base].time, value: candles[i].close });
  }
  return [
    { id: "tenkan", type: "line", color: "#2196f3", lineWidth: 1, data: toLine(candles, tenkan) },
    { id: "kijun", type: "line", color: "#e91e63", lineWidth: 1, data: toLine(candles, kijun) },
    { id: "senkouA", type: "line", color: "#26a69a", lineWidth: 1, data: toLine(candles, senkouA) },
    { id: "senkouB", type: "line", color: "#ef5350", lineWidth: 1, data: toLine(candles, senkouB) },
    { id: "chikou", type: "line", color: "#9e9e9e", lineWidth: 1, data: chikou },
  ];
}

// ----------------------------------------------------------------- registry

// Editable parameters per indicator (drives the params UI; compute() reads them).
export const PARAM_SCHEMA = {
  sma: [{ key: "period", label: "Period", default: 20, min: 1, max: 400 }],
  ema: [{ key: "period", label: "Period", default: 21, min: 1, max: 400 }],
  rsi: [{ key: "period", label: "Period", default: 14, min: 2, max: 100 }],
  atr: [{ key: "period", label: "Period", default: 14, min: 1, max: 100 }],
  bbands: [
    { key: "period", label: "Period", default: 20, min: 2, max: 200 },
    { key: "mult", label: "Std Dev", default: 2, min: 0.5, max: 5, step: 0.5 },
  ],
  macd: [
    { key: "fast", label: "Fast", default: 12, min: 1, max: 100 },
    { key: "slow", label: "Slow", default: 26, min: 2, max: 200 },
    { key: "signalPeriod", label: "Signal", default: 9, min: 1, max: 100 },
  ],
  stoch: [
    { key: "kPeriod", label: "%K", default: 14, min: 1, max: 100 },
    { key: "dPeriod", label: "%D", default: 3, min: 1, max: 50 },
  ],
  ichimoku: [
    { key: "conv", label: "Conversion", default: 9, min: 1, max: 100 },
    { key: "base", label: "Base", default: 26, min: 1, max: 200 },
    { key: "spanB", label: "Span B", default: 52, min: 1, max: 400 },
  ],
  // vwap, fib, volume have no tunable params
};

export const INDICATORS = {
  // free — price overlays
  sma: { label: "SMA", pane: "price", compute: sma },
  ema: { label: "EMA", pane: "price", compute: ema },
  // volume is handled specially by the chart (its own histogram scale)
  // premium — price overlays
  bbands: { label: "Bollinger Bands", pane: "price", compute: bbands },
  vwap: { label: "VWAP", pane: "price", compute: vwap },
  fib: { label: "Fibonacci", pane: "price", compute: fib },
  ichimoku: { label: "Ichimoku Cloud", pane: "price", compute: ichimoku },
  // premium — lower oscillator band (each gets its own band in the chart)
  rsi: { label: "RSI", pane: "lower", compute: rsi },
  macd: { label: "MACD", pane: "lower", compute: macd },
  stoch: { label: "Stochastic", pane: "lower", compute: stoch },
  atr: { label: "ATR", pane: "lower", compute: atr },
};
