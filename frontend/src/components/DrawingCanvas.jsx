// Per-pane drawing overlay (the drawing engine).
//
// A <canvas> overlays the chart and converts stored (time, price) anchors <->
// pixels via the chart API, so shapes stay pinned through pan/zoom/live updates.
//
// Modes (driven by the active tool):
//  - cursor: pointer-events none -> the chart pans/zooms; drawings stay visible.
//  - draw  : a drawing tool is active; clicks collect anchor points and commit.
//  - select: click a shape to select it, drag a handle or its body to move it,
//            Delete/Backspace to remove it.
import { useEffect, useRef } from "react";

export const TOOL_POINTS = {
  trendline: 2, ray: 2, horizontal: 1, vertical: 1, fib: 2, channel: 3, elliott: 6,
};

const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
const HANDLE_R = 6; // px hit radius for anchor handles
const LINE_HIT = 5; // px hit distance for shape bodies

function distToSeg(px, py, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const l2 = dx * dx + dy * dy;
  if (l2 === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (x1 + t * dx), py - (y1 + t * dy));
}

export default function DrawingCanvas({
  api, drawings, tool, color, selectedId,
  onCommit, onActivate, onSelect, onUpdate, onDelete,
}) {
  const canvasRef = useRef(null);
  const st = useRef({
    drawings, tool, color, selectedId,
    points: [], cursor: null, size: { w: 0, h: 0 }, drag: null,
  });
  Object.assign(st.current, { drawings, tool, color, selectedId });

  const mode = tool === "select" ? "select" : tool && tool !== "cursor" ? "draw" : "cursor";

  // --- coordinate helpers ---
  const tx = (t) => api?.chart.timeScale().timeToCoordinate(t);
  const ty = (p) => api?.series.priceToCoordinate(p);
  function dataPoint(e) {
    const r = api.container.getBoundingClientRect();
    const time = api.chart.timeScale().coordinateToTime(e.clientX - r.left);
    const price = api.series.coordinateToPrice(e.clientY - r.top);
    return time == null || price == null ? null : { time, price };
  }
  function mouseXY(e) {
    const r = api.container.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }
  const toPx = (pts) => pts.map((p) => ({ x: tx(p.time), y: ty(p.price), price: p.price }));
  const ok = (q) => q && q.x != null && q.y != null;

  // --- hit testing (returns {type:'handle',index} | {type:'body'} | null) ---
  // Handle pixel position, matching where selection squares are drawn.
  function handlePx(d, i) {
    const x = d.tool === "horizontal" ? 14 : tx(d.points[i].time);
    const y = d.tool === "vertical" ? 14 : ty(d.points[i].price);
    return { x, y };
  }

  function hitTest(d, mx, my) {
    const px = toPx(d.points);
    for (let i = 0; i < d.points.length; i++) {
      const hp = handlePx(d, i);
      if (hp.x != null && hp.y != null && Math.hypot(mx - hp.x, my - hp.y) <= HANDLE_R)
        return { type: "handle", index: i };
    }
    switch (d.tool) {
      case "horizontal": {
        const y = ty(d.points[0].price);
        return y != null && Math.abs(my - y) <= LINE_HIT ? { type: "body" } : null;
      }
      case "vertical": {
        const x = tx(d.points[0].time);
        return x != null && Math.abs(mx - x) <= LINE_HIT ? { type: "body" } : null;
      }
      case "trendline":
        return ok(px[0]) && ok(px[1]) && distToSeg(mx, my, px[0].x, px[0].y, px[1].x, px[1].y) <= LINE_HIT
          ? { type: "body" } : null;
      case "ray": {
        if (!ok(px[0]) || !ok(px[1])) return null;
        const ex = px[0].x + (px[1].x - px[0].x) * 1000;
        const ey = px[0].y + (px[1].y - px[0].y) * 1000;
        return distToSeg(mx, my, px[0].x, px[0].y, ex, ey) <= LINE_HIT ? { type: "body" } : null;
      }
      case "fib": {
        if (!ok(px[0]) || !ok(px[1])) return null;
        const p0 = d.points[0].price, p1 = d.points[1].price;
        for (const L of FIB_LEVELS) {
          const yL = ty(p0 + (p1 - p0) * L);
          if (yL != null && Math.abs(my - yL) <= LINE_HIT && mx >= Math.min(px[0].x, px[1].x)) return { type: "body" };
        }
        return null;
      }
      case "channel":
      case "elliott": {
        for (let i = 0; i < px.length - 1; i++) {
          if (ok(px[i]) && ok(px[i + 1]) &&
            distToSeg(mx, my, px[i].x, px[i].y, px[i + 1].x, px[i + 1].y) <= LINE_HIT)
            return { type: "body" };
        }
        return null;
      }
      default:
        return null;
    }
  }

  function hitAny(mx, my) {
    const ds = st.current.drawings;
    for (let i = ds.length - 1; i >= 0; i--) {
      const h = hitTest(ds[i], mx, my);
      if (h) return { drawing: ds[i], ...h };
    }
    return null;
  }

  // --- rendering ---
  function redraw() {
    const canvas = canvasRef.current;
    if (!canvas || !api) return;
    const { w, h } = st.current.size;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, w, h);

    const drag = st.current.drag;
    for (const d of st.current.drawings) {
      const pts = drag && drag.id === d.id ? drag.working : d.points;
      drawShape(ctx, { ...d, points: pts }, w, h, false, d.id === st.current.selectedId);
    }
    const { tool: t, points, cursor, color: c } = st.current;
    if (mode === "draw" && points.length && cursor) {
      drawShape(ctx, { tool: t, color: c, points: [...points, cursor] }, w, h, true, false);
    }
  }

  function drawShape(ctx, d, w, h, preview, selected) {
    ctx.save();
    ctx.strokeStyle = d.color;
    ctx.fillStyle = d.color;
    ctx.lineWidth = selected ? 2.5 : 1.5;
    if (preview) ctx.setLineDash([5, 4]);
    const px = toPx(d.points);
    const line = (x1, y1, x2, y2) => { ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke(); };
    const dot = (x, y) => { ctx.beginPath(); ctx.arc(x, y, 3, 0, Math.PI * 2); ctx.fill(); };
    const label = (text, x, y) => { ctx.font = "11px sans-serif"; ctx.fillText(text, x, y); };

    switch (d.tool) {
      case "horizontal": { const y = ty(d.points[0].price); if (y != null) { line(0, y, w, y); label(d.points[0].price.toPrecision(6), 4, y - 4); } break; }
      case "vertical": { const x = tx(d.points[0].time); if (x != null) line(x, 0, x, h); break; }
      case "trendline": if (ok(px[0]) && ok(px[1])) { line(px[0].x, px[0].y, px[1].x, px[1].y); } break;
      case "ray": if (ok(px[0]) && ok(px[1])) { const dx = px[1].x - px[0].x, dy = px[1].y - px[0].y; line(px[0].x, px[0].y, px[0].x + dx * 1000, px[0].y + dy * 1000); } break;
      case "fib": if (ok(px[0]) && ok(px[1])) {
        const x0 = Math.min(px[0].x, px[1].x), p0 = d.points[0].price, p1 = d.points[1].price;
        for (const L of FIB_LEVELS) { const yL = ty(p0 + (p1 - p0) * L); if (yL == null) continue; line(x0, yL, w, yL); label(`${(L * 100).toFixed(1)}%`, x0 + 2, yL - 3); }
        line(px[0].x, px[0].y, px[1].x, px[1].y);
      } break;
      case "channel": if (ok(px[0]) && ok(px[1])) {
        line(px[0].x, px[0].y, px[1].x, px[1].y);
        if (ok(px[2])) {
          const vx = px[1].x - px[0].x, vy = px[1].y - px[0].y, wx = px[2].x - px[0].x, wy = px[2].y - px[0].y;
          const t = (wx * vx + wy * vy) / (vx * vx + vy * vy || 1);
          const oxv = px[2].x - (px[0].x + t * vx), oyv = px[2].y - (px[0].y + t * vy);
          line(px[0].x + oxv, px[0].y + oyv, px[1].x + oxv, px[1].y + oyv);
        }
      } break;
      case "elliott": {
        ctx.beginPath(); let started = false;
        px.forEach((q) => { if (!ok(q)) return; started ? ctx.lineTo(q.x, q.y) : ctx.moveTo(q.x, q.y); started = true; });
        ctx.stroke();
        px.forEach((q, i) => { if (ok(q)) label(String(i), q.x + 5, q.y - 5); });
        break;
      }
      default: break;
    }

    // Selection handles (small squares) at each anchor.
    if (selected) {
      ctx.setLineDash([]);
      d.points.forEach((p, i) => {
        const x = d.tool === "horizontal" ? 14 : tx(p.time);
        const y = d.tool === "vertical" ? 14 : ty(p.price);
        if (x == null || y == null) return;
        ctx.fillStyle = "#fff";
        ctx.strokeStyle = d.color;
        ctx.fillRect(x - 4, y - 4, 8, 8);
        ctx.strokeRect(x - 4, y - 4, 8, 8);
        void i;
      });
    } else if (d.tool === "trendline" || d.tool === "ray") {
      px.forEach((q) => ok(q) && dot(q.x, q.y));
    }
    ctx.restore();
  }

  // --- sizing + redraw triggers ---
  useEffect(() => {
    if (!api) return;
    const canvas = canvasRef.current;
    const dpr = window.devicePixelRatio || 1;
    const resize = () => {
      const w = api.container.clientWidth, h = api.container.clientHeight;
      st.current.size = { w, h };
      canvas.width = w * dpr; canvas.height = h * dpr;
      canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
      canvas.getContext("2d").setTransform(dpr, 0, 0, dpr, 0, 0);
      redraw();
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(api.container);
    const ts = api.chart.timeScale();
    const on = () => redraw();
    ts.subscribeVisibleLogicalRangeChange(on);
    api.chart.subscribeCrosshairMove(on);
    return () => { ro.disconnect(); ts.unsubscribeVisibleLogicalRangeChange(on); api.chart.unsubscribeCrosshairMove(on); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);

  useEffect(() => { redraw(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [drawings, tool, color, selectedId, api]);

  useEffect(() => {
    const onKey = (e) => {
      // Ignore when typing in a form field.
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      if (e.key === "Escape") { st.current.points = []; st.current.cursor = null; redraw(); }
      if ((e.key === "Delete" || e.key === "Backspace") && st.current.selectedId) {
        e.preventDefault();
        onDelete?.(st.current.selectedId);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // --- input ---
  function onPointerDown(e) {
    if (mode === "cursor" || !api) return;
    onActivate?.();
    if (mode === "draw") {
      const dp = dataPoint(e);
      if (!dp) return;
      const pts = [...st.current.points, dp];
      if (pts.length >= (TOOL_POINTS[tool] || 2)) {
        onCommit?.({ id: `d${Date.now()}`, tool, color, points: pts });
        st.current.points = []; st.current.cursor = null;
      } else st.current.points = pts;
      redraw();
      return;
    }
    // select mode
    const { x, y } = mouseXY(e);
    const hit = hitAny(x, y);
    if (!hit) { onSelect?.(null); return; }
    onSelect?.(hit.drawing.id);
    const start = dataPoint(e);
    st.current.drag = {
      id: hit.drawing.id,
      mode: hit.type,
      index: hit.index,
      start,
      orig: hit.drawing.points,
      working: hit.drawing.points,
    };
    canvasRef.current.setPointerCapture?.(e.pointerId);
  }

  function onPointerMove(e) {
    if (!api) return;
    if (mode === "draw") { st.current.cursor = dataPoint(e); redraw(); return; }
    if (mode === "select") {
      const drag = st.current.drag;
      if (drag) {
        const cur = dataPoint(e);
        if (!cur) return;
        if (drag.mode === "handle") {
          drag.working = drag.orig.map((p, i) => (i === drag.index ? cur : p));
        } else {
          const dT = cur.time - drag.start.time, dP = cur.price - drag.start.price;
          drag.working = drag.orig.map((p) => ({ time: p.time + dT, price: p.price + dP }));
        }
        redraw();
      } else {
        const { x, y } = mouseXY(e);
        canvasRef.current.style.cursor = hitAny(x, y) ? "move" : "default";
      }
    }
  }

  function onPointerUp() {
    const drag = st.current.drag;
    if (drag) {
      onUpdate?.(drag.id, drag.working);
      st.current.drag = null;
    }
  }

  return (
    <canvas
      ref={canvasRef}
      className="drawing-canvas"
      style={{ pointerEvents: mode === "cursor" ? "none" : "auto", cursor: mode === "draw" ? "crosshair" : "default" }}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    />
  );
}
