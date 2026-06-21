// Symbol search + timeframe switcher for the ACTIVE pane (Section 5).
import { useStore, TIMEFRAMES } from "../store/useStore";
import SymbolSearch from "./SymbolSearch";

export default function SymbolBar() {
  const activePane = useStore((s) => s.activePane());
  const setTimeframe = useStore((s) => s.setTimeframe);

  const timeframe = activePane?.timeframe || "1m";
  const status = activePane?.status || "idle";

  return (
    <div className="symbol-bar">
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
