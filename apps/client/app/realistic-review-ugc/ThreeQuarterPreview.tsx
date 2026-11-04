import { useEffect, useMemo, useRef, useState } from "react";
import type { FrontPlacement } from "./FrontPlacement";
import {
  UV_PROFILE_ID, makeUvTexture, renderSharedUvView,
  type UVBox,
} from "./ThreeQuarterProjection";
import "./ThreeQuarterPreview.css";

type Props = {
  stockSrc: string;
  artworkSrc: string;
  colorKey: string;
  front: FrontPlacement;
  designSize: number;
};

/** Shared UV profile preview. The legacy four-corner controls are intentionally
 * not used: they alter placement after FRONT and disagree with production mesh.
 * A profile error must be corrected in its JSON nodes/mask, for all designs.
 */
export function ThreeQuarterPreview({
  stockSrc, artworkSrc, colorKey, front, designSize,
}: Props) {
  const [debug, setDebug] = useState(false);
  const [stockSize, setStockSize] = useState<{ width: number; height: number } | null>(null);
  const [loaded, setLoaded] = useState<{ src: string; image: HTMLImageElement } | null>(null);
  const [error, setError] = useState("");
  const [box, setBox] = useState<UVBox | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const debugFront = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!artworkSrc) { setLoaded(null); return; }
    let active = true;
    const image = new Image();
    image.onload = () => { if (active) setLoaded({ src: artworkSrc, image }); };
    image.onerror = () => { if (active) { setLoaded(null); setError("Cannot read the original transparent design PNG."); } };
    image.src = artworkSrc;
    return () => { active = false; };
  }, [artworkSrc]);

  const frontInfo = useMemo(() =>
    "FRONT [" + [front.left, front.top, front.width, front.height]
      .map(v => v.toFixed(6)).join(", ") + "] · zoom " + designSize + "%",
    [front.left, front.top, front.width, front.height, designSize]);

  useEffect(() => {
    if (!stockSize || loaded?.src !== artworkSrc || !canvas.current) return;
    const frame = window.requestAnimationFrame(() => {
      if (!canvas.current || loaded?.src !== artworkSrc) return;
      try {
        const source = makeUvTexture(loaded.image, front, designSize);
        renderSharedUvView(
          canvas.current, source, "threeQuarter", stockSize.width, stockSize.height, debug,
        );
        if (debug && debugFront.current)
          renderSharedUvView(debugFront.current, source, "front", 1254, 1254, true);
        setBox(source.box);
        setError("");
      } catch (reason) {
        const ctx = canvas.current.getContext("2d");
        if (ctx) ctx.clearRect(0, 0, canvas.current.width, canvas.current.height);
        setBox(null);
        setError(reason instanceof Error ? reason.message : "Invalid shared UV placement.");
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [stockSize, loaded, artworkSrc, front, designSize, debug]);

  return <article className="rrugc-blueprint-three-quarter-card" aria-label="Three-quarter shared UV projection">
    <header className="rrugc-blueprint-panel-header">
      <div className="rrugc-blueprint-panel-heading">
        <strong>3/4 FRONT · SHARED UV</strong>
        <small>Original PNG · saved FRONT · 5×5 mesh · real occlusion</small>
      </div>
      <div className="rrugc-blueprint-panel-header-actions">
        <button type="button" aria-pressed={debug} disabled={!stockSrc}
          onClick={() => setDebug(value => !value)}>{debug ? "Hide UV debug" : "Debug UV"}</button>
      </div>
    </header>
    <div className="rrugc-blueprint-panel-visual">
      {!stockSrc
        ? <div className="rrugc-blueprint-side-unavailable" role="status">
            Verified 8869 three-quarter stock missing for this color. Sync Hat Images to calibrate.
          </div>
        : <div className="rrugc-blueprint-three-quarter-scene" data-color-key={colorKey}>
          <img src={stockSrc} alt="Official 8869 three-quarter stock" draggable={false}
            onLoad={event => {
              const { naturalWidth, naturalHeight } = event.currentTarget;
              setStockSize(naturalWidth && naturalHeight
                ? { width: naturalWidth, height: naturalHeight } : null);
            }}
            onError={() => setError("Verified 3/4 manufacturer stock could not be loaded.")}/>
          <canvas ref={canvas} aria-label="Embroidery mapped through pinned 5x5 UV mesh and visibility mask"/>
        </div>}
    </div>
    {debug && <div className="rrugc-blueprint-uv-debug">
      <div><strong>FRONT · same atlas</strong><canvas ref={debugFront}
        aria-label="Front UV mesh, placement and centerline"/></div>
      <div className="rrugc-blueprint-uv-debug-stats">
        <strong>Profile: {UV_PROFILE_ID}</strong>
        <small>{frontInfo}</small>
        <small>UV: {box ? box.map(value => value.toFixed(6)).join(" / ") : "waiting"}</small>
        <small>3/4 canvas: {stockSize ? stockSize.width + " × " + stockSize.height : "waiting"}</small>
        <small>FRONT canvas: 1254 × 1254</small>
        <small>Yellow = u 0.5 · purple = design center/baseline · cyan = mesh · red = mask</small>
        <small>Debug lines never enter exported PNG.</small>
      </div>
    </div>}
    {error && <p className="rrugc-blueprint-3q-message is-warning" role="alert">{error}</p>}
  </article>;
}
