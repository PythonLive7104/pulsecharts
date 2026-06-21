// Drawing-tool selector for the active pane. Picking a tool puts the active
// pane into draw mode; after you place a shape it returns to the cursor.
import { useStore } from "../store/useStore";

const TOOLS = [
  { id: "cursor", label: "Cursor (pan/zoom)", icon: "↖" },
  { id: "select", label: "Select / edit drawings", icon: "▭" },
  { id: "trendline", label: "Trend line", icon: "╱" },
  { id: "ray", label: "Ray", icon: "→" },
  { id: "horizontal", label: "Horizontal line", icon: "─" },
  { id: "vertical", label: "Vertical line", icon: "│" },
  { id: "fib", label: "Fibonacci", icon: "⋯" },
  { id: "channel", label: "Parallel channel", icon: "▰" },
  { id: "elliott", label: "Elliott wave", icon: "Ƹ" },
];

const COLORS = ["#4285f4", "#26a69a", "#ef5350", "#f4b400", "#ab47bc", "#ffffff"];

export default function DrawingToolbar() {
  const activeTool = useStore((s) => s.activeTool);
  const setTool = useStore((s) => s.setTool);
  const drawColor = useStore((s) => s.drawColor);
  const setDrawColor = useStore((s) => s.setDrawColor);
  const activePane = useStore((s) => s.activePane());
  const undoDrawing = useStore((s) => s.undoDrawing);
  const clearDrawings = useStore((s) => s.clearDrawings);
  const selected = useStore((s) => s.selected);
  const deleteDrawing = useStore((s) => s.deleteDrawing);

  const count = activePane?.drawings?.length || 0;
  const hasSelection = selected && selected.paneId === activePane?.id;

  return (
    <div className="draw-toolbar">
      {TOOLS.map((t) => (
        <button
          key={t.id}
          className={`draw-tool ${activeTool === t.id ? "active" : ""}`}
          title={t.label}
          onClick={() => setTool(t.id)}
        >
          {t.icon}
        </button>
      ))}

      <span className="draw-sep" />

      <div className="draw-colors">
        {COLORS.map((c) => (
          <button
            key={c}
            className={`swatch ${drawColor === c ? "active" : ""}`}
            style={{ background: c }}
            title={c}
            onClick={() => setDrawColor(c)}
          />
        ))}
      </div>

      <span className="draw-sep" />

      <button
        className="draw-tool"
        title="Delete selected drawing"
        disabled={!hasSelection}
        onClick={() => hasSelection && deleteDrawing(selected.paneId, selected.id)}
      >
        ✕
      </button>
      <button
        className="draw-tool"
        title="Undo last drawing"
        disabled={!count || !activePane}
        onClick={() => activePane && undoDrawing(activePane.id)}
      >
        ⤺
      </button>
      <button
        className="draw-tool"
        title="Clear drawings on this chart"
        disabled={!count || !activePane}
        onClick={() => activePane && clearDrawings(activePane.id)}
      >
        🗑
      </button>
    </div>
  );
}
