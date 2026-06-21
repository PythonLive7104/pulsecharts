// Single browser WS connection to the app's own relay (Section 7).
// Subscribes to the UNION of all panes' (symbol, interval) topics and routes
// each live candle to every pane showing that symbol+timeframe. One socket
// regardless of how many panes are open, so every timeframe ticks live.
//
// Per-connection lifecycle state stays inside the effect closure so a stale
// socket can't resurrect and spawn a connection storm.
import { useEffect, useRef } from "react";
import { useStore } from "../store/useStore";

function wsUrl() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${window.location.host}/ws/market/`;
}

// Topic = "SYMBOL|INTERVAL" — matches the backend demand-registry key.
const topicOf = (symbol, interval) => `${symbol}|${interval}`;
const sub = (ws, symbol, interval) =>
  ws.send(JSON.stringify({ action: "subscribe", symbol, interval }));
const unsub = (ws, symbol, interval) =>
  ws.send(JSON.stringify({ action: "unsubscribe", symbol, interval }));

export function useMarketSocket() {
  const panes = useStore((s) => s.panes);
  const applyLiveCandle = useStore((s) => s.applyLiveCandle);

  // Distinct (symbol, interval) topics currently displayed across all panes.
  const topics = [
    ...new Set(panes.filter((p) => p.symbol).map((p) => topicOf(p.symbol, p.timeframe))),
  ].sort();
  const topicsKey = topics.join(",");

  const wsRef = useRef(null);
  const wantedRef = useRef(new Set());
  const subbedRef = useRef(new Set());
  wantedRef.current = new Set(topics);

  useEffect(() => {
    let active = true;
    let backoff = 1000;
    let reconnectTimer = null;
    let ws = null;

    function connect() {
      if (!active) return;
      ws = new WebSocket(wsUrl());
      wsRef.current = ws;
      ws.onopen = () => {
        backoff = 1000;
        subbedRef.current = new Set();
        for (const t of wantedRef.current) {
          const [symbol, interval] = t.split("|");
          sub(ws, symbol, interval);
          subbedRef.current.add(t);
        }
      };
      ws.onmessage = (evt) => {
        const msg = JSON.parse(evt.data);
        if (msg.type === "candle") applyLiveCandle(msg.data);
      };
      ws.onclose = () => {
        if (!active) return;
        reconnectTimer = setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30000);
      };
      ws.onerror = () => ws.close();
    }

    connect();
    return () => {
      active = false;
      clearTimeout(reconnectTimer);
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
      if (wsRef.current === ws) wsRef.current = null;
    };
  }, [applyLiveCandle]);

  // Reconcile subscriptions when the set of displayed topics changes.
  useEffect(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    const wanted = new Set(topicsKey ? topicsKey.split(",") : []);
    const subbed = subbedRef.current;
    for (const t of subbed) {
      if (!wanted.has(t)) {
        const [symbol, interval] = t.split("|");
        unsub(ws, symbol, interval);
        subbed.delete(t);
      }
    }
    for (const t of wanted) {
      if (!subbed.has(t)) {
        const [symbol, interval] = t.split("|");
        sub(ws, symbol, interval);
        subbed.add(t);
      }
    }
  }, [topicsKey]);
}
