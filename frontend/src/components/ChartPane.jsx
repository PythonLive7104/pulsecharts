// One chart cell in the multi-chart grid. Wraps the pure Chart with a label,
// per-pane loading/error overlay, the drawing overlay, and click-to-activate.
import { useState } from "react";
import Chart from "./Chart";
import DrawingCanvas from "./DrawingCanvas";
import { useStore } from "../store/useStore";
import { priceDecimals } from "../lib/price";

export default function ChartPane({ pane }) {
  const activePaneId = useStore((s) => s.activePaneId);
  const setActivePane = useStore((s) => s.setActivePane);
  const loadCandlesFor = useStore((s) => s.loadCandlesFor);
  const multi = useStore((s) => s.panes.length > 1);
  const activeTool = useStore((s) => s.activeTool);
  const drawColor = useStore((s) => s.drawColor);
  const addDrawing = useStore((s) => s.addDrawing);
  const setTool = useStore((s) => s.setTool);
  const selected = useStore((s) => s.selected);
  const selectDrawing = useStore((s) => s.selectDrawing);
  const updateDrawing = useStore((s) => s.updateDrawing);
  const deleteDrawing = useStore((s) => s.deleteDrawing);

  const symbols = useStore((s) => s.symbols);

  const [api, setApi] = useState(null);
  const isActive = pane.id === activePaneId;
  const selectedId = selected?.paneId === pane.id ? selected.id : null;
  // Price precision depends on the symbol's asset class (forex needs 3–5 dp).
  const sym = symbols.find((x) => x.ticker === pane.symbol);
  const precision = priceDecimals(sym?.asset_class, pane.symbol);

  return (
    <div
      className={`chart-pane ${isActive && multi ? "active-pane" : ""}`}
      onMouseDown={() => setActivePane(pane.id)}
    >
      {multi && (
        <div className="pane-label">
          {pane.symbol || "—"} · {pane.timeframe}
        </div>
      )}
      <Chart
        candles={pane.candles}
        activeIndicators={pane.indicators}
        indicatorParams={pane.params}
        precision={precision}
        onReady={setApi}
      />
      {api && (
        <DrawingCanvas
          api={api}
          drawings={pane.drawings}
          // A tool only acts on the active pane; non-active panes stay in cursor mode.
          tool={isActive ? activeTool : "cursor"}
          color={drawColor}
          selectedId={selectedId}
          onActivate={() => setActivePane(pane.id)}
          onCommit={(drawing) => {
            if (drawing.tool === "cross") {
              // Single movable crosshair: reposition the existing cross instead of
              // stacking duplicates, and stay in cross mode so each click moves it
              // freely. (Other tools commit once and switch to select for editing.)
              const existing = pane.drawings.find((d) => d.tool === "cross");
              if (existing) updateDrawing(pane.id, existing.id, drawing.points);
              else addDrawing(pane.id, drawing);
              return;
            }
            addDrawing(pane.id, drawing);
            setTool("select"); // jump to select so the new shape is editable
            selectDrawing(pane.id, drawing.id); // auto-select the new shape
          }}
          onSelect={(id) => selectDrawing(pane.id, id)}
          onUpdate={(id, points) => updateDrawing(pane.id, id, points)}
          onDelete={(id) => deleteDrawing(pane.id, id)}
        />
      )}
      {pane.status === "loading" && (
        <div className="chart-overlay">
          <div className="spinner" />
          <span>Loading {pane.symbol}…</span>
        </div>
      )}
      {pane.status === "error" && (
        <div className="chart-overlay">
          <span className="error">⚠ {pane.error || "Failed to load chart data."}</span>
          <button className="btn-ghost" onClick={() => loadCandlesFor(pane.id)}>Retry</button>
        </div>
      )}
      {pane.status !== "loading" && pane.status !== "error" &&
        pane.candles.length === 0 && pane.symbol && (
          <div className="chart-overlay"><span className="muted">No data for {pane.symbol}.</span></div>
        )}
    </div>
  );
}
