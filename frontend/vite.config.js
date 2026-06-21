import { defineConfig, createLogger } from "vite";
import react from "@vitejs/plugin-react";

// Custom logger that drops benign WebSocket-proxy reset noise (ECONNRESET /
// "ws proxy error" / "http proxy error") that fires on every HMR reload,
// page refresh, or StrictMode remount. Real errors still pass through.
const logger = createLogger();
const NOISE = /ECONNRESET|EPIPE|ws proxy error|http proxy error/i;
const origError = logger.error;
const origWarn = logger.warn;
logger.error = (msg, opts) => {
  if (typeof msg === "string" && NOISE.test(msg)) return;
  origError(msg, opts);
};
logger.warn = (msg, opts) => {
  if (typeof msg === "string" && NOISE.test(msg)) return;
  origWarn(msg, opts);
};

// Dev proxy so the SPA can call the Django backend without CORS friction.
// REST -> /api, WebSocket relay -> /ws (Section 7, 9).
export default defineConfig({
  plugins: [react()],
  customLogger: logger,
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true },
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
        // Swallow socket-reset errors so an abrupt WS close doesn't crash the
        // proxy or spam the console; the client reconnects on its own.
        configure: (proxy) => {
          proxy.on("error", (err) => {
            if (!NOISE.test(err?.code || "") && !NOISE.test(err?.message || "")) {
              // Re-surface genuine proxy errors.
              console.error("[ws proxy]", err.message);
            }
          });
        },
      },
    },
  },
});
