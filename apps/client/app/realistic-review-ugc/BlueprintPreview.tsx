import { Fragment, useRef, useState, type TouchEvent } from "react";
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

export const BLUEPRINT_WINDOW_SIZE = 4;

export function blueprintWindow(versions: GenerationOutputVersion[], start: number, size = BLUEPRINT_WINDOW_SIZE) {
  const offset = Math.max(0, Math.min(Math.max(0, versions.length - size), start));
  return { offset, items: versions.slice(offset, offset + size) };
}

function spritePosition(index: number) {
  return {
    backgroundPosition: `${(index % 4) / 3 * 100}% ${Math.floor(index / 4) / 2 * 100}%`,
  };
}

function designName(version: GenerationOutputVersion) {
  const source = version.output_name?.split(/[\\/]/).filter(Boolean).pop();
  return source || `Design ${version.version}`;
}

/** Preview-only 12-color mockup; never submits a generation or modifies stored outputs. */
export function BlueprintPreview({ versions, initialVersion }: {
  versions: GenerationOutputVersion[];
  initialVersion?: number;
}) {
  const initialIndex = initialVersion ? versions.findIndex(item => item.version === initialVersion) : 0;
  const [start, setStart] = useState(() => Math.max(0, initialIndex));
  const [scale, setScale] = useState(100);
  const [guides, setGuides] = useState(false);
  const touchStart = useRef<number | null>(null);
  const { offset, items } = blueprintWindow(versions, start);
  const lastStart = Math.max(0, versions.length - BLUEPRINT_WINDOW_SIZE);
  const canPrevious = offset > 0;
  const canNext = offset < lastStart;
  const move = (step: number) => setStart(current => Math.max(0, Math.min(lastStart, current + step)));

  function onTouchEnd(event: TouchEvent<HTMLDivElement>) {
    if (touchStart.current === null) return;
    const delta = event.changedTouches[0]?.clientX - touchStart.current;
    touchStart.current = null;
    if (Math.abs(delta) > 45) move(delta < 0 ? 1 : -1);
  }

  return <section className="rrugc-blueprint" aria-label="Blueprint · 12 hat colors by design">
    <div className="rrugc-blueprint-controls">
      <div className="rrugc-blueprint-description">
        <strong>12 hat colors × {versions.length} designs</strong>
        <span>Each column is a design. Scroll down through colors; navigate sideways to compare designs.</span>
      </div>
      <div className="rrugc-blueprint-options">
        <label className="rrugc-blueprint-guides">
          <input type="checkbox" checked={guides} onChange={event => setGuides(event.target.checked)} /> Guides
        </label>
        <label className="rrugc-blueprint-scale">Design size
          <input type="range" min="60" max="140" step="5" value={scale}
            onChange={event => setScale(Number(event.target.value))} aria-label="Blueprint design size" />
          <b>{scale}%</b>
        </label>
        <div className="rrugc-blueprint-nav" aria-label="Navigate designs">
          <button type="button" aria-label="Previous design columns" disabled={!canPrevious}
            onClick={() => move(-1)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>
          </button>
          <span>{versions.length ? `${offset + 1}–${offset + items.length} / ${versions.length}` : "0 designs"}</span>
          <button type="button" aria-label="Next design columns" disabled={!canNext}
            onClick={() => move(1)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="m9 18 6-6-6-6" /></svg>
          </button>
        </div>
      </div>
    </div>
    <p className="rrugc-blueprint-disclaimer" role="note">
      Digital placement preview only · Approximate embroidery scale and position. Use the production proof for exact dimensions and stitch appearance.
    </p>
    <div className="rrugc-blueprint-viewport" tabIndex={0}
      aria-label="12 color mockup comparison matrix. Use left and right arrow keys to change designs."
      onKeyDown={event => {
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          move(event.key === "ArrowRight" ? 1 : -1);
        }
      }}
      onTouchStart={event => { touchStart.current = event.touches[0]?.clientX ?? null; }}
      onTouchEnd={onTouchEnd}>
      <div className="rrugc-blueprint-matrix" role="table" aria-label="12 hat colors with design overlays"
        style={{ gridTemplateColumns: `134px repeat(${items.length}, minmax(196px, 1fr))` }}>
        <div className="rrugc-blueprint-corner" role="columnheader"><span>COLORWAY</span><strong>8869 Twill Cap</strong></div>
        {items.map((design, index) => <div className="rrugc-blueprint-heading" role="columnheader" key={design.version}>
          <span>DESIGN {offset + index + 1}</span>
          <strong title={designName(design)}>{designName(design)}</strong>
          <a href={design.url} target="_blank" rel="noreferrer" title="Open original design">Original ↗</a>
        </div>)}
        {BLUEPRINT_HAT_COLORS.map((color, index) => <Fragment key={color.id}>
          <div className="rrugc-blueprint-color" role="rowheader">
            <span className="rrugc-blueprint-color-count">{String(index + 1).padStart(2, "0")}</span>
            <strong>{color.short}</strong><small>Natural / {color.short}</small>
          </div>
          {items.map(design => <div role="cell" className="rrugc-blueprint-cell" key={color.id + ":" + design.version}
            aria-label={color.label + " · " + designName(design)}>
            <div className="rrugc-blueprint-hat" style={spritePosition(index)}>
              <div className={"rrugc-blueprint-design-zone" + (guides ? " is-guided" : "")}>
                <img src={design.url + (design.url.includes("?") ? "&" : "?") + "thumbnail=true&size=300"}
                  loading="lazy" decoding="async"
                  style={{ transform: `scale(${scale / 100})` }}
                  alt={"Preview design " + designName(design) + " on " + color.label} />
              </div>
            </div>
          </div>)}
        </Fragment>)}
      </div>
    </div>
    <div className="rrugc-blueprint-footnote">
      <span>Base hat: 8869 · front view · 12 supplied color references</span>
      <span>Preview only · No AI generation required</span>
    </div>
  </section>;
}
