import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { FrontPlacement } from "./FrontPlacement";
import type { ThreeQuarterPlacement } from "./api";
import { DEFAULT_THREE_QUARTER_CORNERS } from "./ThreeQuarterPlacement";
import "./ThreeQuarterPreview.css";

type Props = {
  stockSrc: string;
  artworkSrc: string;
  colorKey: string;
  front: FrontPlacement;
  designSize: number;
  saved: ThreeQuarterPlacement | null;
  onSave: (value: ThreeQuarterPlacement) => Promise<ThreeQuarterPlacement>;
};

type Point = [number, number];
const bound = (x: number) => Math.min(1, Math.max(0, x));

/* Split the image into narrow vertical slices: curved top/bottom boundaries
   and changing slice widths approximate the cylindrical cap panel. */
function renderProjection(canvas: HTMLCanvasElement, img: HTMLImageElement, corners: Point[], scale: number) {
  const w = 1000, h = 1000;
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);
  if (!img.naturalWidth || !img.naturalHeight) return;
  const ratio = Math.min(1, Math.max(0.4, scale));
  const center: Point = [corners.reduce((s, p) => s + p[0], 0) / 4, corners.reduce((s, p) => s + p[1], 0) / 4];
  const [tl, tr, br, bl] = corners.map(p => [center[0] + (p[0] - center[0]) * ratio, center[1] + (p[1] - center[1]) * ratio] as Point);
  for (let i = 0; i < 80; i++) {
    const t = i / 80, next = (i + 1) / 80;
    const drawStrip = (u: number) => {
      const curve = 0.012 * Math.sin(Math.PI * u);
      const top: Point = [(tl[0] + (tr[0] - tl[0]) * u) * w, (tl[1] + (tr[1] - tl[1]) * u - curve) * h];
      const bot: Point = [(bl[0] + (br[0] - bl[0]) * u) * w, (bl[1] + (br[1] - bl[1]) * u + curve) * h];
      return { top, bot };
    };
    const a = drawStrip(t), b = drawStrip(next);
    const sourceX = t * img.naturalWidth;
    const sourceW = (next - t) * img.naturalWidth;
    const sourceTop = 0;
    const sourceH = img.naturalHeight;
    const sourceSlice = 2;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(a.top[0], a.top[1]); ctx.lineTo(b.top[0], b.top[1]);
    ctx.lineTo(b.bot[0], b.bot[1]); ctx.lineTo(a.bot[0], a.bot[1]);
    ctx.closePath(); ctx.clip();
    const dx = b.top[0] - a.top[0], dy = b.top[1] - a.top[1];
    const vx = a.bot[0] - a.top[0], vy = a.bot[1] - a.top[1];
    ctx.setTransform(dx / sourceW, dy / sourceW, vx / sourceH, vy / sourceH,
      a.top[0] - (dx / sourceW) * sourceX - (vx / sourceH) * sourceTop,
      a.top[1] - (dy / sourceW) * sourceX - (vy / sourceH) * sourceTop);
    ctx.drawImage(img, Math.max(0, sourceX - sourceSlice), sourceTop,
      Math.min(img.naturalWidth - Math.max(0, sourceX - sourceSlice), sourceW + 2 * sourceSlice),
      sourceH, Math.max(0, sourceX - sourceSlice), sourceTop, sourceW + 2 * sourceSlice, sourceH);
    ctx.restore();
  }
}

export function ThreeQuarterPreview({ stockSrc, artworkSrc, colorKey, front, designSize, saved, onSave }: Props) {
  const [draft, setDraft] = useState<Point[]>(saved?.corners ?? DEFAULT_THREE_QUARTER_CORNERS);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const canvas = useRef<HTMLCanvasElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const dragging = useRef<number | null>(null);
  const changed = JSON.stringify(draft) !== JSON.stringify(saved?.corners ?? DEFAULT_THREE_QUARTER_CORNERS);
  useEffect(() => { if (!editing) setDraft(saved?.corners ?? DEFAULT_THREE_QUARTER_CORNERS); }, [saved, editing, colorKey]);
  useEffect(() => {
    if (!stockSrc || !artworkSrc || !canvas.current) return;
    const image = new Image();
    let active = true;
    image.onload = () => { if (active && canvas.current) renderProjection(canvas.current, image, draft,
      (Math.min(1, front.width / 0.696) * designSize / 100)); };
    image.src = artworkSrc;
    return () => { active = false; };
  }, [draft, artworkSrc, stockSrc, front.width, designSize]);
  function changePoint(index: number, x: number, y: number) {
    setDraft(points => points.map((p, i) => i === index ? [bound(x), bound(y)] : p));
  }
  function movePointer(event: PointerEvent<HTMLElement>) {
    if (dragging.current === null) return;
    const rect = surface.current?.getBoundingClientRect();
    if (!rect?.width || !rect?.height) return;
    event.preventDefault();
    changePoint(dragging.current, (event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height);
  }
  async function save() {
    if (saving || !saved) return;
    setSaving(true); setMessage("");
    try {
      await onSave({ ...saved, corners: draft });
      setEditing(false); setMessage("3/4 placement saved");
    } catch (e) { setMessage(e instanceof Error ? e.message : "Could not save 3/4 placement"); }
    finally { setSaving(false); }
  }
  return <article className="rrugc-blueprint-three-quarter-card" aria-label="Three-quarter embroidery preview">
    <header className="rrugc-blueprint-panel-header">
      <div className="rrugc-blueprint-panel-heading"><strong>3/4 FRONT</strong><small>Projected from saved FRONT size</small></div>
      <div className="rrugc-blueprint-panel-header-actions">
        {editing ? <>
          <button type="button" disabled={saving || !changed} onClick={() => void save()}>{saving ? "Saving…" : "Save"}</button>
          <button type="button" onClick={() => { setEditing(false); setDraft(saved?.corners ?? DEFAULT_THREE_QUARTER_CORNERS); setMessage(""); }}>Cancel</button>
          <button type="button" onClick={() => setDraft(DEFAULT_THREE_QUARTER_CORNERS.map(point => [...point] as Point))}>Reset</button>
        </> : <button type="button" disabled={!stockSrc || !saved} onClick={() => { setEditing(true); setMessage(""); }}>Edit 4 points</button>}
      </div>
    </header>
    <div className="rrugc-blueprint-panel-visual">
      {!stockSrc ? <div className="rrugc-blueprint-side-unavailable" role="status">Verified 8869 three-quarter stock missing for this color. Sync Hat Images to calibrate.</div> :
        <div className="rrugc-blueprint-three-quarter-scene" ref={surface} onPointerMove={movePointer}
          onPointerUp={event => { dragging.current = null; if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }}
          onPointerCancel={() => { dragging.current = null; }}>
          <img src={stockSrc} alt="Official 8869 three-quarter stock" draggable={false}/>
          <canvas ref={canvas} aria-label="Artwork projected over the curved crown" />
          {editing && <>
            <svg className="rrugc-blueprint-three-quarter-outline" viewBox="0 0 1000 1000" preserveAspectRatio="none" aria-hidden="true">
              <polygon points={draft.map(p => p[0]*1000 + "," + p[1]*1000).join(" ")} />
            </svg>
            {draft.map(([x, y], i) => <button key={i} type="button" className="rrugc-blueprint-three-quarter-handle"
              style={{ left: (x * 100) + "%", top: (y * 100) + "%" }} aria-label={["Top left", "Top right", "Bottom right", "Bottom left"][i] + " embroidery corner"}
              onPointerDown={event => { event.preventDefault(); event.stopPropagation(); dragging.current = i; surface.current?.setPointerCapture?.(event.pointerId); }}
              onKeyDown={event => { const dx = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0; const dy = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
                if (dx || dy) { event.preventDefault(); changePoint(i, x + dx * (event.shiftKey ? 0.02 : 0.005), y + dy * (event.shiftKey ? 0.02 : 0.005)); } }}>{i+1}</button>)}
          </>}
        </div>}
    </div>
    {message && <p className="rrugc-blueprint-3q-message" role="status">{message}</p>}
  </article>;
}
