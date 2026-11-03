import { useEffect, useRef, useState, type PointerEvent } from "react";
import type { FrontPlacement } from "./FrontPlacement";
import type { ThreeQuarterPlacement } from "./api";
import {
  CROWN_FIT_CORNERS_8869, FRONT_CROWN_MASK_8869,
  insideCrown, isValidQuad, renderCrownProjection, type Point,
} from "./ThreeQuarterProjection";
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

const bound = (x: number) => Math.min(1, Math.max(0, x));

export function ThreeQuarterPreview({
  stockSrc, artworkSrc, colorKey, front, designSize, saved, onSave,
}: Props) {
  const savedUsable = Boolean(saved?.corners && isValidQuad(saved.corners) && saved.corners.every(point => insideCrown(point)));
  const initialCorners = savedUsable && saved ? saved.corners : CROWN_FIT_CORNERS_8869;
  const [draft, setDraft] = useState<Point[]>(initialCorners);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [loaded, setLoaded] = useState<{ src: string; image: HTMLImageElement } | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const surface = useRef<HTMLDivElement>(null);
  const dragging = useRef<number | null>(null);
  const changed = JSON.stringify(draft) !== JSON.stringify(saved?.corners ?? CROWN_FIT_CORNERS_8869);
  const validQuad = isValidQuad(draft);
  const withinCrown = draft.every(point => insideCrown(point));
  const saveAllowed = validQuad && withinCrown && changed && !saving && Boolean(saved);

  // Reuse the decoded, stitched/alpha artwork on every point drag. Never
  // refetch the 1024px PNG just because a guide point moved.
  useEffect(() => {
    if (!artworkSrc) return;
    let active = true;
    const image = new Image();
    image.onload = () => { if (active) setLoaded({ src: artworkSrc, image }); };
    image.onerror = () => { if (active) setLoaded(null); };
    image.src = artworkSrc;
    return () => { active = false; };
  }, [artworkSrc]);

  useEffect(() => {
    if (!editing) setDraft(initialCorners);
  }, [saved, editing, colorKey]);

  useEffect(() => {
    if (!stockSrc || !canvas.current) return;
    const frame = window.requestAnimationFrame(() => {
      if (!canvas.current) return;
      if (loaded?.src !== artworkSrc) {
        // Reset the bitmap when switching artwork; no context allocation before load.
        canvas.current.width = canvas.current.width;
        return;
      }
      renderCrownProjection(canvas.current, loaded.image, {
        corners: draft,
        front,
        designSize,
      });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [draft, artworkSrc, loaded, stockSrc, front, designSize]);

  function changePoint(index: number, x: number, y: number) {
    setDraft(points => points.map((p, i) => i === index ? [bound(x), bound(y)] : p));
  }

  function movePointer(event: PointerEvent<HTMLDivElement>) {
    if (dragging.current === null) return;
    const rect = surface.current?.getBoundingClientRect();
    if (!rect?.width || !rect?.height) return;
    event.preventDefault();
    changePoint(dragging.current, (event.clientX - rect.left) / rect.width,
      (event.clientY - rect.top) / rect.height);
  }

  async function save() {
    if (!saveAllowed || !saved) return;
    setSaving(true); setMessage("");
    try {
      await onSave({ ...saved, corners: draft });
      setEditing(false);
      setMessage("3/4 placement saved. Projected embroidery follows the crown.");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "Could not save 3/4 placement");
    } finally {
      setSaving(false);
    }
  }

  return <article className="rrugc-blueprint-three-quarter-card" aria-label="Three-quarter embroidery preview">
    <header className="rrugc-blueprint-panel-header">
      <div className="rrugc-blueprint-panel-heading">
        <strong>3/4 FRONT</strong><small>Fit to crown · saved FRONT size</small>
      </div>
      <div className="rrugc-blueprint-panel-header-actions">
        {editing ? <>
          <button type="button" disabled={!saveAllowed} onClick={() => void save()}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" disabled={saving} onClick={() => {
            setEditing(false); setDraft(initialCorners); setMessage("");
          }}>Cancel</button>
          <button type="button" disabled={saving} onClick={() => {
            setDraft(CROWN_FIT_CORNERS_8869.map(point => [...point] as Point)); setMessage("");
          }} title="Fit all four points to the verified crown area">Reset</button>
        </> : <button type="button" disabled={!stockSrc || !saved} onClick={() => {
          setEditing(true); setMessage("");
        }}>Edit 4 points</button>}
      </div>
    </header>
    <div className="rrugc-blueprint-panel-visual">
      {!stockSrc
        ? <div className="rrugc-blueprint-side-unavailable" role="status">
            Verified 8869 three-quarter stock missing for this color. Sync Hat Images to calibrate.
          </div>
        : <div className="rrugc-blueprint-three-quarter-scene" ref={surface}
            onPointerMove={movePointer}
            onPointerUp={event => {
              dragging.current = null;
              if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
                event.currentTarget.releasePointerCapture(event.pointerId);
              }
            }}
            onPointerCancel={() => { dragging.current = null; }}>
            <img src={stockSrc} alt="Official 8869 three-quarter stock" draggable={false}/>
            <canvas ref={canvas} aria-label="Four-point projected embroidery, curved and clipped to front crown"/>
            {editing && <>
              <svg className="rrugc-blueprint-three-quarter-outline" viewBox="0 0 1000 1000"
                preserveAspectRatio="none" aria-hidden="true">
                <polygon className="rrugc-blueprint-three-quarter-crown-mask"
                  points={FRONT_CROWN_MASK_8869.map(p => p[0] * 1000 + "," + p[1] * 1000).join(" ")}/>
                <polygon className="rrugc-blueprint-three-quarter-field"
                  points={draft.map(p => p[0] * 1000 + "," + p[1] * 1000).join(" ")}/>
              </svg>
              {draft.map(([x, y], i) => <button key={i} type="button"
                className={"rrugc-blueprint-three-quarter-handle" + (!insideCrown([x, y]) ? " is-outside-crown" : "")}
                style={{ left: (x * 100) + "%", top: (y * 100) + "%" }}
                aria-label={["Top left", "Top right", "Bottom right", "Bottom left"][i] + " embroidery corner"}
                onPointerDown={event => {
                  event.preventDefault(); event.stopPropagation();
                  dragging.current = i;
                  surface.current?.setPointerCapture?.(event.pointerId);
                }}
                onKeyDown={event => {
                  const dx = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
                  const dy = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
                  if (dx || dy) {
                    event.preventDefault();
                    changePoint(i, x + dx * (event.shiftKey ? 0.02 : 0.005),
                      y + dy * (event.shiftKey ? 0.02 : 0.005));
                  }
                }}>{i + 1}</button>)}
            </>}
          </div>}
    </div>
    {saved && !savedUsable && !editing && <p className="rrugc-blueprint-3q-message" role="status">Previous 3/4 anchors were outside the front crown. Preview uses a safe field; choose Edit 4 points → Save to keep it.</p>}
    {editing && (!validQuad || !withinCrown) && <p className="rrugc-blueprint-3q-message is-warning" role="alert">
      {!validQuad ? "The four points must not overlap or cross." :
        "A point is outside the front crown. Drag it inside the dotted crown boundary, or use Reset."}
    </p>}
    {message && <p className="rrugc-blueprint-3q-message" role="status">{message}</p>}
  </article>;
}
