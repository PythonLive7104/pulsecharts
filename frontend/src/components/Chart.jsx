// Candlestick chart with volume + indicators (Section 5, 10) — lightweight-charts v5.
// Price overlays (SMA/EMA/Bollinger/VWAP/Fib/Ichimoku) live in the main pane;
// volume and each oscillator (RSI/MACD/Stochastic/ATR) get their OWN pane via
// the v5 panes API, so every band has its own price axis and scales independently.
import { useEffect, useRef } from "react";
import {
  createChart,
  CrosshairMode,
  CandlestickSeries,
  LineSeries,
  HistogramSeries,
} from "lightweight-charts";
import { useTheme } from "../theme";
import { INDICATORS } from "../lib/indicators";

const CHART_THEME = {
  dark: { bg: "#0e1117", text: "#d1d4dc", grid: "#1c2230" },
  light: { bg: "#ffffff", text: "#1f2933", grid: "#e8edf4" },
};

// Stable band order so each active oscillator keeps its position.
const OSC_ORDER = ["rsi", "macd", "stoch", "atr"];
const VOLUME_KEY = "__volume__:vol";

export default function Chart({ candles, activeIndicators, indicatorParams, precision = 2, onReady }) {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const candleSeriesRef = useRef(null);
  const seriesRef = useRef({}); // key -> { series, paneIndex }
  const sigRef = useRef("");

  const theme = useTheme((s) => s.theme);

  // Create the chart once.
  useEffect(() => {
    const c = CHART_THEME[theme] || CHART_THEME.dark;
    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: { background: { color: c.bg }, textColor: c.text },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      timeScale: { timeVisible: true, secondsVisible: false },
    });
    chartRef.current = chart;
    candleSeriesRef.current = chart.addSeries(CandlestickSeries, {
      upColor: "#26a69a",
      downColor: "#ef5350",
      borderVisible: false,
      wickUpColor: "#26a69a",
      wickDownColor: "#ef5350",
    });
    // Expose chart + price series + container so an overlay (drawing tools)
    // can convert between (time, price) and pixels.
    onReady?.({ chart, series: candleSeriesRef.current, container: containerRef.current });

    return () => {
      chart.remove();
      seriesRef.current = {};
      sigRef.current = "";
      onReady?.(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Restyle on theme toggle.
  useEffect(() => {
    if (!chartRef.current) return;
    const c = CHART_THEME[theme] || CHART_THEME.dark;
    chartRef.current.applyOptions({
      layout: { background: { color: c.bg }, textColor: c.text },
      grid: { vertLines: { color: c.grid }, horzLines: { color: c.grid } },
    });
  }, [theme]);

  // Price precision: forex needs 3–5 dp, crypto 2. lightweight-charts defaults
  // to 2 dp / 0.01 minMove, which would round EUR/USD's 1.08542 to 1.08.
  useEffect(() => {
    if (!candleSeriesRef.current) return;
    candleSeriesRef.current.applyOptions({
      priceFormat: { type: "price", precision, minMove: 1 / 10 ** precision },
    });
  }, [precision]);

  // Sync candles + indicator series + pane layout.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !candleSeriesRef.current) return;
    candleSeriesRef.current.setData(candles);

    // Pane layout: pane 0 = price; then volume, then each oscillator.
    const oscillators = OSC_ORDER.filter((s) => activeIndicators.has(s));
    const hasVolume = activeIndicators.has("volume");
    const paneList = [];
    if (hasVolume) paneList.push("__volume__");
    oscillators.forEach((s) => paneList.push(s));
    const paneIndexOf = (entry) => 1 + paneList.indexOf(entry);
    const signature = paneList.join(",");

    // Desired series specs keyed "slug:id".
    const desired = {};
    if (hasVolume) {
      desired[VOLUME_KEY] = {
        type: "histogram",
        isVolume: true,
        paneIndex: paneIndexOf("__volume__"),
        data: candles.map((c) => ({
          time: c.time,
          value: c.volume,
          color: c.close >= c.open ? "#26a69a66" : "#ef535066",
        })),
      };
    }
    for (const slug of activeIndicators) {
      const def = INDICATORS[slug];
      if (!def || !def.compute) continue;
      const paneIndex = def.pane === "lower" ? paneIndexOf(slug) : 0;
      for (const spec of def.compute(candles, indicatorParams[slug] || {})) {
        desired[`${slug}:${spec.id}`] = { ...spec, paneIndex };
      }
    }

    const existing = seriesRef.current;

    // On pane-structure change, drop all tracked series so they're recreated in
    // the right panes (cheap — only happens when toggling volume/oscillators).
    if (signature !== sigRef.current) {
      for (const key of Object.keys(existing)) {
        chart.removeSeries(existing[key].series);
        delete existing[key];
      }
      sigRef.current = signature;
    }

    // Remove series no longer wanted.
    for (const key of Object.keys(existing)) {
      if (!desired[key]) {
        chart.removeSeries(existing[key].series);
        delete existing[key];
      }
    }

    // Create/update.
    for (const [key, spec] of Object.entries(desired)) {
      let rec = existing[key];
      if (!rec || rec.paneIndex !== spec.paneIndex) {
        if (rec) chart.removeSeries(rec.series);
        const series =
          spec.type === "histogram"
            ? chart.addSeries(
                HistogramSeries,
                spec.isVolume
                  ? { priceFormat: { type: "volume" }, priceScaleId: "right", lastValueVisible: false }
                  : { color: spec.color, lastValueVisible: false },
                spec.paneIndex
              )
            : chart.addSeries(
                LineSeries,
                {
                  color: spec.color,
                  lineWidth: spec.lineWidth ?? 2,
                  priceLineVisible: false,
                  lastValueVisible: false,
                  crosshairMarkerVisible: false,
                },
                spec.paneIndex
              );
        rec = existing[key] = { series, paneIndex: spec.paneIndex };
      }
      rec.series.setData(spec.data);
    }

    // Volume bars sit at the bottom of their own pane.
    if (hasVolume) {
      chart.priceScale("right", paneIndexOf("__volume__")).applyOptions({
        scaleMargins: { top: 0.1, bottom: 0 },
      });
    }

    // Prune leftover empty panes, then size: price pane dominates.
    const need = 1 + paneList.length;
    while (chart.panes().length > need) {
      chart.removePane(chart.panes().length - 1);
    }
    chart.panes().forEach((p, i) => p.setStretchFactor(i === 0 ? 4 : 1));
  }, [candles, activeIndicators, indicatorParams]);

  return <div className="chart" ref={containerRef} />;
}
