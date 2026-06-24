// Asset-class toggle + symbol search + timeframe switcher for the ACTIVE pane
// (Section 5). The toggle filters the picker between crypto (Hyperliquid) and
// forex (Twelve Data) symbols.
import { useStore, TIMEFRAMES } from "../store/useStore";
import SymbolSearch from "./SymbolSearch";

const ASSET_CLASSES = [
  ["crypto", "Crypto"],
  ["forex", "Forex"],
];

export default function SymbolBar() {
  const activePane = useStore((s) => s.activePane());
  const setTimeframe = useStore((s) => s.setTimeframe);
  const assetClass = useStore((s) => s.assetClass);
  const setAssetClass = useStore((s) => s.setAssetClass);

  const timeframe = activePane?.timeframe || "1m";
  const status = activePane?.status || "idle";

  return (
    <div className="symbol-bar">
      <div className="asset-toggle">
        {ASSET_CLASSES.map(([key, label]) => (
          <button
            key={key}
            className={key === assetClass ? "asset-btn active" : "asset-btn"}
            onClick={() => setAssetClass(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <SymbolSearch />
      <div className="timeframes">
        {TIMEFRAMES.map((tf) => (
          <button
            key={tf}
            className={tf === timeframe ? "tf active" : "tf"}
            onClick={() => activePane && setTimeframe(activePane.id, tf)}
          >
            {tf}
          </button>
        ))}
      </div>
      <span className={`status status-${status}`}>{status}</span>
    </div>
  );
}
