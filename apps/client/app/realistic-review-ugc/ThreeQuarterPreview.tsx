import { useEffect, useMemo, useRef, useState } from "react";
import type { FrontPlacement } from "./FrontPlacement";
import {
  UV_PROFILE_ID, drawUvDebug, makeUvTexture, renderSharedUvView,
  type UVBox,
} from "./ThreeQuarterProjection";
import "./ThreeQuarterPreview.css";

type Props = {
  stockSrc: string;
  frontStockSrc: string;
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
  stockSrc, frontStockSrc, artworkSrc, colorKey, front, designSize,
}: Props) {
  const [debug, setDebug] = useState(false);
  const [stockSize, setStockSize] = useState<{ source: string; width: number; height: number } | null>(null);
  const [loaded, setLoaded] = useState<{ src: string; image: HTMLImageElement } | null>(null);
  const [frontStock, setFrontStock] = useState<{ src: string; image: HTMLImageElement } | null>(null);
  const [error, setError] = useState("");
  const [box, setBox] = useState<UVBox | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const debugOverlay = useRef<HTMLCanvasElement>(null);
  const debugFront = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    if (!artworkSrc) { setLoaded(null); return; }
    setError("");
    let active = true;
    const image = new Image();
    image.onload = () => { if (active) setLoaded({ src: artworkSrc, image }); };
    image.onerror = () => { if (active) { setLoaded(null); setError("Cannot read the original transparent design PNG."); } };
    image.src = artworkSrc;
    return () => { active = false; };
  }, [artworkSrc]);

  useEffect(() => {
    if (!debug || !frontStockSrc) return;
    let active = true;
    const image = new Image();
    image.onload = () => {
      if (active && image.naturalWidth === 1254 && image.naturalHeight === 1254)
        setFrontStock({ src: frontStockSrc, image });
    };
    image.src = frontStockSrc;
    return () => { active = false; };
  }, [debug, frontStockSrc]);

  // Keep one cached original PNG/atlas across the 12 colorways; when stock
  // changes, clear the previous design until that color's photo has loaded.
  // Never briefly show the last color's projection on another hat.
  useEffect(() => {
    // Reset canvas pixels without constructing a new image/UV texture.
    // A dimension assignment clears pixels in both real browsers and jsdom.
    if (canvas.current) canvas.current.width = canvas.current.width;
    if (debugOverlay.current) debugOverlay.current.width = debugOverlay.current.width;
  }, [stockSrc, artworkSrc]);

  const frontInfo = useMemo(() =>
    "FRONT [" + [front.left, front.top, front.width, front.height]
      .map(v => v.toFixed(6)).join(", ") + "] · zoom " + designSize + "%",
    [front.left, front.top, front.width, front.height, designSize]);

  // The original source can be full resolution. Alpha inspect + texture creation
  // run only when artwork or FRONT placement changes, never for every hat
  // color, toggle or render of the parent Blueprint.
  const prepared = useMemo(() => {
    if (loaded?.src !== artworkSrc) return null;
    try {
      return { value: makeUvTexture(loaded.image, front, designSize), error: "" };
    } catch (cause) {
      return { value: null, error: cause instanceof Error ? cause.message : "Invalid original PNG." };
    }
  }, [
    loaded, artworkSrc, front.left, front.top, front.width, front.height, designSize,
  ]);

  useEffect(() => {
    if (!stockSize || stockSize.source !== stockSrc || !prepared || !canvas.current) return;
    const frame = window.requestAnimationFrame(() => {
      if (!canvas.current || stockSize.source !== stockSrc) return;
      if (!prepared.value) {
        canvas.current.getContext("2d")?.clearRect(0, 0, canvas.current.width, canvas.current.height);
        setBox(null);
        setError(prepared.error || "Cannot create shared UV texture.");
        return;
      }
      try {
        // This primary design layer is ALWAYS clean. Debug never changes it,
        // including when somebody exports the canvas from browser tools.
        renderSharedUvView(
          canvas.current, prepared.value, "threeQuarter",
          stockSize.width, stockSize.height,
        );
        if (debug && debugOverlay.current) {
          const overlay = debugOverlay.current;
          overlay.width = stockSize.width;
          overlay.height = stockSize.height;
          const overlayContext = overlay.getContext("2d");
          if (overlayContext) {
            overlayContext.clearRect(0, 0, overlay.width, overlay.height);
            drawUvDebug(
              overlayContext, "threeQuarter", prepared.value.box,
              overlay.width, overlay.height,
            );
          }
        }
        if (debug && debugFront.current) {
          const frontPreview = debugFront.current;
          frontPreview.width = 1254; frontPreview.height = 1254;
          const frontContext = frontPreview.getContext("2d");
          if (frontContext) {
            if (frontStock?.src === frontStockSrc)
              frontContext.drawImage(frontStock.image, 0, 0, 1254, 1254);
            const frontInk = document.createElement("canvas");
            renderSharedUvView(frontInk, prepared.value, "front", 1254, 1254);
            frontContext.drawImage(frontInk, 0, 0);
            drawUvDebug(frontContext, "front", prepared.value.box, 1254, 1254);
          }
        }
        setBox(prepared.value.box);
        setError("");
      } catch (reason) {
        canvas.current.getContext("2d")?.clearRect(0, 0, canvas.current.width, canvas.current.height);
        setBox(null);
        setError(reason instanceof Error ? reason.message : "Invalid shared UV placement.");
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [stockSize, stockSrc, prepared, debug, frontStock, frontStockSrc]);

  return <article className="rrugc-blueprint-three-quarter-card" aria-label="Three-quarter shared UV projection"
    data-uv-original-src={artworkSrc} data-front-stock-src={frontStockSrc}>
    <header className="rrugc-blueprint-panel-header">
      <div className="rrugc-blueprint-panel-heading">
        <strong>3/4 FRONT · SHARED UV</strong>
        <small>Source PNG · saved FRONT · 5×5 mesh · real occlusion</small>
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
                ? { source: stockSrc, width: naturalWidth, height: naturalHeight } : null);
            }}
            onError={() => setError("Verified 3/4 manufacturer stock could not be loaded.")}/>
          <canvas ref={canvas} aria-label="Clean design mapped through pinned 5x5 UV mesh and visibility mask"/>
          {debug && <canvas ref={debugOverlay} className="rrugc-blueprint-three-quarter-debug-overlay"
            aria-label="Debug mesh, design centerline and visibility mask only"/>}
        </div>}
    </div>
    {debug && <div className="rrugc-blueprint-uv-debug">
      <div><strong>FRONT · stock + same atlas</strong><canvas ref={debugFront}
        aria-label="Front UV mesh, placement and centerline"/></div>
      <div className="rrugc-blueprint-uv-debug-stats">
        <strong>Profile: {UV_PROFILE_ID}</strong>
        <small>{frontInfo}</small>
        <small>UV: {box ? box.map(value => value.toFixed(6)).join(" / ") : "waiting"}</small>
        <small>3/4 canvas: {stockSize?.source === stockSrc ? stockSize.width + " × " + stockSize.height : "waiting"}</small>
        <small>FRONT canvas: 1254 × 1254 · {frontStock?.src === frontStockSrc ? "stock loaded" : "stock loading"}</small>
        <small>Yellow = u 0.5 · purple = design center/baseline · cyan = mesh · red = mask</small>
        <small>Clean canvas and debug overlay are isolated layers.</small>
      </div>
    </div>}
    {error && <p className="rrugc-blueprint-3q-message is-warning" role="alert">{error}</p>}
  </article>;
}