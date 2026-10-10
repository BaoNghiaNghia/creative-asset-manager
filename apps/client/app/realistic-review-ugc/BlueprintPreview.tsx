import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { GenerationOutputVersion } from "./api";
import "./BlueprintPreview.css";

export const BLUEPRINT_HAT_COLORS = [
  { id: "black", label: "Natural / Black", short: "Black" },
  { id: "brown", label: "Natural / Brown", short: "Brown" },
  { id: "camo-green", label: "Natural / Camo Green", short: "Camo Green" },
  { id: "charcoal", label: "Natural / Charcoal", short: "Charcoal" },
  { id: "forest-green", label: "Natural / Forest Green", short: "Forest Green" },
  { id: "khaki", label: "Natural / Khaki", short: "Khaki" },
  { id: "maroon", label: "Natural / Maroon", short: "Maroon" },
  { id: "mossy-oak-breakup", label: "Natural / Mossy Oak", short: "Mossy Oak" },
  { id: "navy", label: "Natural / Navy", short: "Navy" },
  { id: "realtree-all-purpose", label: "Natural / Realtree", short: "Realtree" },
  { id: "red", label: "Natural / Red", short: "Red" },
  { id: "royal", label: "Natural / Royal", short: "Royal" },
] as const;

export const BLUEPRINT_WINDOW_SIZE = 5;

export function blueprintWindow(versions: GenerationOutputVersion[], focusIndex: number, size = BLUEPRINT_WINDOW_SIZE) {
  const width = Math.min(Math.max(1, size), versions.length);
  const offset = Math.max(0, Math.min(Math.max(0, versions.length - width), focusIndex - Math.floor(width / 2)));
  return { offset, items: versions.slice(offset, offset + width) };
}

const clamp = (value: number, count: number) => Math.max(0, Math.min(count - 1, value));

export function blueprintNavigate(
  designIndex: number, colorIndex: number, direction: "left" | "right" | "up" | "down",
  designCount: number, colorCount = BLUEPRINT_HAT_COLORS.length,
) {
  return {
    design: clamp(designIndex + (direction === "right" ? 1 : direction === "left" ? -1 : 0), designCount),
    color: clamp(colorIndex + (direction === "down" ? 1 : direction === "up" ? -1 : 0), colorCount),
  };
}

export function blueprintHatPhoto(colorId: string) {
  return "/rrugc/blueprint/fronts/" + encodeURIComponent(colorId) + ".jpg";
}

function designName(version: GenerationOutputVersion) {
  return version.output_name?.split(/[\\/]/).filter(Boolean).pop() || "Design " + version.version;
}

function thumbnailUrl(url: string, size: number) {
  return url + (url.includes("?") ? "&" : "?") + "thumbnail=true&size=" + size;
}

type DragAxis = "horizontal" | "vertical" | "both";
type DragOrigin = { x: number; y: number; pointerId: number; axis: DragAxis };

/** Only near neighbors are mounted; the active thumbnail and hat stay physically centered. */
export function BlueprintPreview({ versions, initialVersion }: {
  versions: GenerationOutputVersion[];
  initialVersion?: number;
}) {
  const initialIndex = initialVersion ? versions.findIndex(item => item.version === initialVersion) : 0;
  const [designIndex, setDesignIndex] = useState(Math.max(0, initialIndex));
  const [colorIndex, setColorIndex] = useState(0);
  const [scale, setScale] = useState(100);
  const [guides, setGuides] = useState(false);
  const dragOrigin = useRef<DragOrigin | null>(null);
  const suppressNextClick = useRef(false);
  const selectedDesign = clamp(designIndex, versions.length);
  const activeDesign = versions[selectedDesign];
  const activeColor = BLUEPRINT_HAT_COLORS[colorIndex];

  const move = (direction: "left" | "right" | "up" | "down", steps = 1) => {
    if (direction === "left" || direction === "right") {
      setDesignIndex(index => clamp(index + (direction === "right" ? steps : -steps), versions.length));
    } else {
      setColorIndex(index => clamp(index + (direction === "down" ? steps : -steps), BLUEPRINT_HAT_COLORS.length));
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      const target = event.target as HTMLElement | null;
      if (typeof target?.closest === "function" && target.closest("input,textarea,select,[contenteditable=true]")) return;
      const direction: Record<string, "left" | "right" | "up" | "down"> = {
        ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down",
      };
      if (!direction[event.key]) return;
      event.preventDefault();
      move(direction[event.key]);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>, axis: DragAxis) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if ((event.target as HTMLElement).closest("a,input")) return;
    dragOrigin.current = { x: event.clientX, y: event.clientY, pointerId: event.pointerId, axis };
    suppressNextClick.current = false;
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = dragOrigin.current;
    if (!start || start.pointerId !== event.pointerId) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) >= 8) {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    }
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const origin = dragOrigin.current;
    dragOrigin.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture?.(event.pointerId);
    if (!origin || origin.pointerId !== event.pointerId) return;
    const deltaX = event.clientX - origin.x;
    const deltaY = event.clientY - origin.y;
    const axis = origin.axis === "both" ? Math.abs(deltaX) >= Math.abs(deltaY) ? "horizontal" : "vertical" : origin.axis;
    const delta = axis === "horizontal" ? deltaX : deltaY;
    if (Math.abs(delta) < 35) return;
    suppressNextClick.current = true;
    const steps = Math.max(1, Math.round(Math.abs(delta) / (axis === "horizontal" ? 166 : 88)));
    move(axis === "horizontal" ? delta < 0 ? "right" : "left" : delta < 0 ? "down" : "up", steps);
    window.setTimeout(() => { suppressNextClick.current = false; }, 0);
  };
  const clickCapture = (event: React.MouseEvent<HTMLDivElement>) => {
    if (suppressNextClick.current) {
      event.preventDefault();
      event.stopPropagation();
      suppressNextClick.current = false;
    }
  };

  // Index-based strips shift around fixed center anchors, with only seven nodes per axis.
  const neighbors = [-3, -2, -1, 0, 1, 2, 3];
  if (!activeDesign) return <div className="rrugc-blueprint-empty">No designs available to preview.</div>;

  return <section className="rrugc-blueprint rrugc-blueprint-cross" aria-label="Blueprint cross puzzle · design and hat color navigator">
    <div className="rrugc-blueprint-controls">
      <div className="rrugc-blueprint-description">
        <strong>Blueprint <span className="rrugc-blueprint-count">{versions.length} designs × 12 hat colors</span></strong>
        <span>Slide the design row sideways and the hat colors vertically; the selected mockup stays centered.</span>
      </div>
      <div className="rrugc-blueprint-options">
        <label className="rrugc-blueprint-guides"><input type="checkbox" checked={guides}
          onChange={event => setGuides(event.target.checked)} /> Show guides</label>
        <label className="rrugc-blueprint-scale">Design size
          <input type="range" min="40" max="100" step="5" value={scale}
            onChange={event => setScale(Number(event.target.value))} aria-label="Blueprint design size" />
          <b>{scale}%</b>
        </label>
      </div>
    </div>
    <div className="rrugc-blueprint-cross-layout">
      <div className="rrugc-blueprint-cross-corner">
        <span>8869 TWILL CAP</span><strong>12 colorways</strong><small>↑ ↓ Hat colors</small>
      </div>
      <div className="rrugc-blueprint-design-carousel" aria-label="Designs carousel">
        <button type="button" className="rrugc-blueprint-design-control is-prev"
          aria-label="Previous design columns" disabled={selectedDesign === 0} onClick={() => move("left")}>‹</button>
        <div className="rrugc-blueprint-design-track" role="group"
          aria-label="Drag horizontally to select designs" onPointerDown={event => onPointerDown(event,"horizontal")}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          onPointerCancel={() => { dragOrigin.current = null; }} onClickCapture={clickCapture}>
          {neighbors.map(delta => {
            const index = selectedDesign + delta;
            const design = versions[index];
            if (!design) return null;
            return <button type="button" key={design.version}
              className={"rrugc-blueprint-design-choice" + (delta === 0 ? " is-selected" : "")}
              style={{ transform: "translateX(calc(-50% + " + (delta * 174) + "px))" }}
              aria-pressed={delta === 0} aria-label={"Select design " + designName(design)}
              onClick={() => setDesignIndex(index)}>
              <span className="rrugc-blueprint-design-caption">DESIGN {index + 1}{delta === 0 ? " · SELECTED" : ""}</span>
              <img src={thumbnailUrl(design.url, 240)} alt="" draggable={false} loading="lazy" decoding="async"/>
              <strong title={designName(design)}>{designName(design)}</strong>
            </button>;
          })}
        </div>
        <button type="button" className="rrugc-blueprint-design-control is-next"
          aria-label="Next design columns" disabled={selectedDesign >= versions.length - 1} onClick={() => move("right")}>›</button>
      </div>

      <div className="rrugc-blueprint-color-carousel" aria-label="Hat color carousel">
        <button type="button" className="rrugc-blueprint-color-control is-prev" aria-label="Previous hat color"
          disabled={colorIndex === 0} onClick={() => move("up")}>↑</button>
        <div className="rrugc-blueprint-color-track" role="group" aria-label="Drag vertically to select hat colors"
          onPointerDown={event => onPointerDown(event,"vertical")} onPointerMove={onPointerMove}
          onPointerUp={onPointerUp} onPointerCancel={() => { dragOrigin.current = null; }} onClickCapture={clickCapture}>
          {neighbors.map(delta => {
            const index = colorIndex + delta;
            const color = BLUEPRINT_HAT_COLORS[index];
            if (!color) return null;
            return <button type="button" key={color.id}
              className={"rrugc-blueprint-color-choice" + (delta === 0 ? " is-selected" : "")}
              style={{ transform: "translate(-50%, calc(-50% + " + (delta * 94) + "px))" }}
              aria-label={"Select hat color " + color.label} aria-pressed={delta === 0}
              onClick={() => setColorIndex(index)}>
              <img className="rrugc-blueprint-color-hat" src={blueprintHatPhoto(color.id)}
                alt="" loading="lazy" decoding="async" draggable={false}/>
              <span className="rrugc-blueprint-color-name">{color.short}</span>
            </button>;
          })}
        </div>
        <button type="button" className="rrugc-blueprint-color-control is-next" aria-label="Next hat color"
          disabled={colorIndex === BLUEPRINT_HAT_COLORS.length - 1} onClick={() => move("down")}>↓</button>
      </div>

      <div className="rrugc-blueprint-preview-area" role="region" aria-label="Fixed centered selected hat mockup">
        <div className="rrugc-blueprint-selected-card" onPointerDown={event => onPointerDown(event,"both")}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          onPointerCancel={() => { dragOrigin.current = null; }}>
          <div className="rrugc-blueprint-selected-top">
            <span className="rrugc-blueprint-selected-badge">● SELECTED</span>
            <span>{selectedDesign + 1} / {versions.length} designs · {colorIndex + 1} / 12 colors</span>
          </div>
          <div className="rrugc-blueprint-selected-hat">
            <img className="rrugc-blueprint-hat-original" src={blueprintHatPhoto(activeColor.id)}
              alt={"Valucap 8869 original cap front, " + activeColor.label}
              loading="eager" decoding="async" draggable={false}/>
            <div className={"rrugc-blueprint-selected-design" + (guides ? " is-guided" : "")}>
              <img key={activeDesign.version} src={thumbnailUrl(activeDesign.url, 512)}
                style={{ transform: "scale(" + (scale / 100) + ")" }}
                alt={designName(activeDesign) + " mockup on " + activeColor.label}
                loading="lazy" decoding="async" draggable={false}/>
            </div>
          </div>
          <div className="rrugc-blueprint-selected-info" role="status" aria-live="polite">
            <strong title={designName(activeDesign)}>{designName(activeDesign)}</strong>
            <span>{activeColor.label}</span>
          </div>
          <a className="rrugc-blueprint-original" href={activeDesign.url} target="_blank" rel="noreferrer">View original design ↗</a>
        </div>
        <div className="rrugc-blueprint-nav-hint">← → change design <span>·</span> ↑ ↓ change hat color <span>·</span> drag to browse</div>
      </div>
    </div>
    <div className="rrugc-blueprint-footnote">
      <span>Placement is approximate; verify embroidery proof before production.</span>
      <span>12 supplied 8869 references · No AI generation required</span>
    </div>
  </section>;
}
