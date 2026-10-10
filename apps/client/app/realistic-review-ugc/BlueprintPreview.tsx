import { Fragment, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
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

/** Keep a five-design window centered around the active puzzle column. */
export function blueprintWindow(versions: GenerationOutputVersion[], focusIndex: number, size = BLUEPRINT_WINDOW_SIZE) {
  const width = Math.min(Math.max(1, size), versions.length);
  const offset = Math.max(0, Math.min(Math.max(0, versions.length - width), focusIndex - Math.floor(width / 2)));
  return { offset, items: versions.slice(offset, offset + width) };
}

export function blueprintNavigate(
  designIndex: number, colorIndex: number, direction: "left" | "right" | "up" | "down",
  designCount: number, colorCount = BLUEPRINT_HAT_COLORS.length,
) {
  const design = Math.max(0, Math.min(Math.max(0, designCount - 1),
    designIndex + (direction === "right" ? 1 : direction === "left" ? -1 : 0)));
  const color = Math.max(0, Math.min(Math.max(0, colorCount - 1),
    colorIndex + (direction === "down" ? 1 : direction === "up" ? -1 : 0)));
  return { design, color };
}

function spritePosition(index: number) {
  return { backgroundPosition: `${index % 4 / 3 * 100}% ${Math.floor(index / 4) / 2 * 100}%` };
}

function designName(version: GenerationOutputVersion) {
  const filename = version.output_name?.split(/[\\/]/).filter(Boolean).pop();
  return filename || `Design ${version.version}`;
}

type DragStart = { x: number; y: number; pointerId: number };

/** A 2-axis puzzle navigator: horizontal designs, vertical colorways, one focused crosshair. */
export function BlueprintPreview({ versions, initialVersion }: {
  versions: GenerationOutputVersion[];
  initialVersion?: number;
}) {
  const initialIndex = initialVersion ? versions.findIndex(item => item.version === initialVersion) : 0;
  const [designIndex, setDesignIndex] = useState(Math.max(0, initialIndex));
  const [colorIndex, setColorIndex] = useState(0);
  const [scale, setScale] = useState(100);
  const [guides, setGuides] = useState(false);
  const drag = useRef<DragStart | null>(null);
  const suppressClick = useRef(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const currentDesignIndex = Math.min(Math.max(0, versions.length - 1), designIndex);
  const { offset, items } = blueprintWindow(versions, currentDesignIndex);

  const navigate = (direction: "left" | "right" | "up" | "down", steps = 1) => {
    const next = blueprintNavigate(currentDesignIndex, colorIndex, direction, versions.length);
    const change = direction === "left" || direction === "right"
      ? { ...next, design: Math.max(0, Math.min(Math.max(0, versions.length - 1),
          currentDesignIndex + (direction === "right" ? steps : -steps))) }
      : { ...next, color: Math.max(0, Math.min(BLUEPRINT_HAT_COLORS.length - 1,
          colorIndex + (direction === "down" ? steps : -steps))) };
    setDesignIndex(change.design);
    setColorIndex(change.color);
  };

  useEffect(() => {
    // Modal-only listener: arrow navigation works without clicking the grid first.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.altKey || event.ctrlKey || event.metaKey) return;
      const target = event.target as HTMLElement | null;
      if (typeof target?.closest === "function" && target.closest("input,textarea,select,[contenteditable=true]")) return;
      const direction = {
        ArrowLeft: "left", ArrowRight: "right", ArrowUp: "up", ArrowDown: "down",
      }[event.key];
      if (!direction) return;
      event.preventDefault();
      navigate(direction as "left" | "right" | "up" | "down");
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  });

  useEffect(() => {
    // Keep the focused hat visible below the sticky design header.
    const viewport = viewportRef.current;
    const cell = viewport?.querySelector<HTMLElement>(".rrugc-blueprint-cell.is-active-cell");
    if (!viewport || !cell) return;
    const view = viewport.getBoundingClientRect();
    const rect = cell.getBoundingClientRect();
    const stickyHeaderHeight = viewport.querySelector(".rrugc-blueprint-heading")?.getBoundingClientRect().height ?? 165;
    const nextTop = rect.top < view.top + stickyHeaderHeight
      ? Math.max(0, viewport.scrollTop - (view.top + stickyHeaderHeight - rect.top) - 12)
      : rect.bottom > view.bottom ? viewport.scrollTop + rect.bottom - view.bottom + 12 : viewport.scrollTop;
    // On narrow screens the chosen puzzle column may initially be off screen.
    const stickyColorWidth = viewport.querySelector(".rrugc-blueprint-corner")?.getBoundingClientRect().width ?? 140;
    const nextLeft = rect.left < view.left + stickyColorWidth
      ? Math.max(0, viewport.scrollLeft - (view.left + stickyColorWidth - rect.left) - 8)
      : rect.right > view.right ? viewport.scrollLeft + rect.right - view.right + 8 : viewport.scrollLeft;
    if (nextTop !== viewport.scrollTop || nextLeft !== viewport.scrollLeft) {
      viewport.scrollTo?.({ top: nextTop, left: nextLeft, behavior: "smooth" });
    }
  }, [colorIndex, currentDesignIndex]);

  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if ((event.target as HTMLElement).closest("a,input")) return;
    drag.current = { x: event.clientX, y: event.clientY, pointerId: event.pointerId };
    suppressClick.current = false;
  };

  const onPointerUp = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = drag.current;
    drag.current = null;
    if (!start) return;
    const dx = event.clientX - start.x;
    const dy = event.clientY - start.y;
    if (Math.max(Math.abs(dx), Math.abs(dy)) < 40) return;
    suppressClick.current = true;
    if (Math.abs(dx) >= Math.abs(dy)) {
      navigate(dx < 0 ? "right" : "left", Math.max(1, Math.round(Math.abs(dx) / 170)));
    } else {
      navigate(dy < 0 ? "down" : "up", Math.max(1, Math.round(Math.abs(dy) / 180)));
    }
    window.setTimeout(() => { suppressClick.current = false; }, 0);
  };

  const focusDesign = versions[currentDesignIndex];
  const focusColor = BLUEPRINT_HAT_COLORS[colorIndex];

  return <section className="rrugc-blueprint" aria-label="Blueprint puzzle · 12 hat colors by design">
    <div className="rrugc-blueprint-controls">
      <div className="rrugc-blueprint-description">
        <strong>Blueprint Puzzle <span className="rrugc-blueprint-count">{BLUEPRINT_HAT_COLORS.length} colors × {versions.length} designs</span></strong>
        <span>Designs across · Hat colors down · Drag the grid or use ← → ↑ ↓ to navigate</span>
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
      </div>
    </div>
    <div className="rrugc-blueprint-focusbar">
      <div className="rrugc-blueprint-focus-label" role="status" aria-live="polite">
        <span className="rrugc-blueprint-focus-square" aria-hidden="true" />
        <span><b>{focusDesign ? designName(focusDesign) : "No design"}</b>
          <small>{focusColor.label} · {currentDesignIndex + 1}/{versions.length} designs · {colorIndex + 1}/12 colors</small></span>
      </div>
      <div className="rrugc-blueprint-direction-controls" role="group" aria-label="Blueprint puzzle navigation">
        <button type="button" aria-label="Previous design columns" disabled={currentDesignIndex <= 0}
          onClick={() => navigate("left")} title="Previous design (←)">←</button>
        <button type="button" aria-label="Next design columns" disabled={currentDesignIndex >= versions.length - 1}
          onClick={() => navigate("right")} title="Next design (→)">→</button>
        <span aria-hidden="true" className="rrugc-blueprint-control-separator" />
        <button type="button" aria-label="Previous hat color" disabled={colorIndex === 0}
          onClick={() => navigate("up")} title="Previous hat color (↑)">↑</button>
        <button type="button" aria-label="Next hat color" disabled={colorIndex === BLUEPRINT_HAT_COLORS.length - 1}
          onClick={() => navigate("down")} title="Next hat color (↓)">↓</button>
      </div>
    </div>
    <div className="rrugc-blueprint-viewport" ref={viewportRef} tabIndex={0}
      aria-label="Blueprint puzzle matrix: left/right selects the highlighted design column; up/down selects the hat color"
      onPointerDown={onPointerDown}
      onPointerMove={event => {
        const start = drag.current;
        if (!start || Math.max(Math.abs(event.clientX - start.x), Math.abs(event.clientY - start.y)) < 9) return;
        if (!event.currentTarget.hasPointerCapture?.(event.pointerId)) {
          event.currentTarget.setPointerCapture?.(event.pointerId);
        }
      }}
      onPointerUp={event => {
        onPointerUp(event);
        if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture?.(event.pointerId);
      }}
      onPointerCancel={() => { drag.current = null; }}
      onClickCapture={event => { if (suppressClick.current) { event.preventDefault(); event.stopPropagation(); suppressClick.current = false; } }}>
      <div className="rrugc-blueprint-matrix" role="table" aria-label="12 hat colors across design columns"
        style={{ gridTemplateColumns: `140px repeat(${items.length}, minmax(190px, 1fr))` }}>
        <div className="rrugc-blueprint-corner" role="columnheader">
          <span>12 COLORWAYS</span><strong>8869 Twill Cap</strong><small>↑ ↓ Navigate colors</small>
        </div>
        {items.map((design, index) => <div role="columnheader"
          key={design.version}
          className={"rrugc-blueprint-heading" + (offset + index === currentDesignIndex ? " is-active-column" : "")}>
          <button type="button" className="rrugc-blueprint-heading-button"
            aria-pressed={offset + index === currentDesignIndex}
            aria-label={"Select design " + designName(design)}
            onClick={() => setDesignIndex(offset + index)}>
            <span className="rrugc-blueprint-heading-index">DESIGN {offset + index + 1}{offset + index === currentDesignIndex ? " · SELECTED" : ""}</span>
            <img src={design.url + (design.url.includes("?") ? "&" : "?") + "thumbnail=true&size=220"}
              alt="" loading="lazy" decoding="async" draggable={false} />
            <strong title={designName(design)}>{designName(design)}</strong>
          </button>
          <a href={design.url} target="_blank" rel="noreferrer" title="Open original design">Open original ↗</a>
        </div>)}
        {BLUEPRINT_HAT_COLORS.map((color, row) => <Fragment key={color.id}>
          <div className={"rrugc-blueprint-color" + (row === colorIndex ? " is-active-row" : "")} role="rowheader">
            <span className="rrugc-blueprint-color-count">{String(row + 1).padStart(2, "0")}</span>
            <strong>{color.short}</strong><small>{color.label}</small>
            {row === colorIndex && <span className="rrugc-blueprint-row-marker">ACTIVE</span>}
          </div>
          {items.map((design, index) => {
            const activeColumn = offset + index === currentDesignIndex;
            const activeCell = activeColumn && row === colorIndex;
            return <button type="button" role="cell" key={color.id + ":" + design.version}
              className={"rrugc-blueprint-cell" + (activeColumn ? " is-active-column" : "") +
                (row === colorIndex ? " is-active-row" : "") + (activeCell ? " is-active-cell" : "")}
              aria-label={color.label + " · " + designName(design)}
              aria-pressed={activeCell}
              onClick={() => { setDesignIndex(offset + index); setColorIndex(row); }}>
              <div className="rrugc-blueprint-hat" style={spritePosition(row)}>
                <div className={"rrugc-blueprint-design-zone" + (guides ? " is-guided" : "")}>
                  <img src={design.url + (design.url.includes("?") ? "&" : "?") + "thumbnail=true&size=300"}
                    loading="lazy" decoding="async"
                    style={{ transform: `scale(${scale / 100})` }}
                    alt={"Preview design " + designName(design) + " on " + color.label}
                    draggable={false} />
                </div>
              </div>
              {activeCell && <span className="rrugc-blueprint-cell-indicator">SELECTED</span>}
            </button>;
          })}
        </Fragment>)}
      </div>
    </div>
    <div className="rrugc-blueprint-footnote">
      <span>Reference mockups only · Design placement and scale are approximate; verify the embroidery proof before production.</span>
      <span>12 real hat references · No AI generation required</span>
    </div>
  </section>;
}
