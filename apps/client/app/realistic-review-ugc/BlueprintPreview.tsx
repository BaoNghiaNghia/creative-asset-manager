import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { GenerationOutputVersion } from "./api";
import { DeferredImage } from "./DeferredImage";
import { BlueprintSmoothImage, prefetchBlueprintImage } from "./BlueprintSmoothImage";
import { hideBlueprintLens, moveBlueprintLens } from "./BlueprintMagnifier";
import { BlueprintSideGuides } from "./BlueprintSideGuides";
import { useBlueprintArtworkFit } from "./BlueprintDesignFit";
import { BlueprintViewZoom } from "./BlueprintViewZoom";
import { useBlueprintEmbroideryTint } from "./BlueprintEmbroideryTint";
import { BLUEPRINT_DARK_PALETTES, selectBlueprintThreadPalette } from "./BlueprintThreadPalettes";
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

export const BLUEPRINT_SIDE_ATLAS = "/rrugc/blueprint/sides-atlas-fit.webp";
export const BLUEPRINT_SIDE_CALIBRATION_DESIGN = "/rrugc/blueprint/roberts-reference.png";

// The trimmed 3 × 4 atlas contains entire side caps; display one full cell
// without zooming or cropping any part of the brim or crown.
export function blueprintSidePosition(colorIndex: number) {
  const col = colorIndex % 3;
  const row = Math.floor(colorIndex / 3);
  return { backgroundPosition: `${col * 50}% ${(row * 100) / 3}%` };
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
  const [frontDesignSize, setFrontDesignSize] = useState(100);
  const [sideDesignSize, setSideDesignSize] = useState(100);
  const [paletteOverrides, setPaletteOverrides] = useState<Record<string, number>>({});
  const [guides, setGuides] = useState(false);
  const [sideAtlasReady, setSideAtlasReady] = useState(false);
  const [sideAtlasFailed, setSideAtlasFailed] = useState(false);
  const dragOrigin = useRef<DragOrigin | null>(null);
  const suppressNextClick = useRef(false);
  const frontLens = useRef<HTMLDivElement>(null);
  const sideLens = useRef<HTMLDivElement>(null);
  const selectedDesign = clamp(designIndex, versions.length);
  const activeDesign = versions[selectedDesign];
  const activeColor = BLUEPRINT_HAT_COLORS[colorIndex];
  const activeDesignSrc = activeDesign?.url ? thumbnailUrl(activeDesign.url, 512) : "";
  const paletteKey = activeColor.id + ":" + (activeDesign?.version ?? "none");
  const activePaletteOrdinal = paletteOverrides[paletteKey] ?? selectedDesign;
  const activePalette = selectBlueprintThreadPalette(activeColor.id, activePaletteOrdinal);
  const paletteCount = (BLUEPRINT_DARK_PALETTES[activeColor.id] ?? BLUEPRINT_DARK_PALETTES.black).length;
  const cyclePalette = () => setPaletteOverrides(current => ({ ...current, [paletteKey]: (activePaletteOrdinal + 1) % paletteCount }));
  const tintedFrontSrc = useBlueprintEmbroideryTint(activeDesignSrc, activeColor.id, activePaletteOrdinal);
  const tintedSideSrc = useBlueprintEmbroideryTint(BLUEPRINT_SIDE_CALIBRATION_DESIGN, activeColor.id, activePaletteOrdinal);
  const paletteSwatch = "linear-gradient(90deg, " + activePalette.primary + " 0 52%, " + activePalette.secondary + " 52% 80%, " + activePalette.accent + " 80% 100%)";
  const frontFit = useBlueprintArtworkFit(activeDesignSrc, frontDesignSize);

  useEffect(() => {
    hideBlueprintLens(frontLens.current);
    hideBlueprintLens(sideLens.current);
  }, [activeColor.id, activeDesign?.url]);

  const move = useCallback((direction: "left" | "right" | "up" | "down", steps = 1) => {
    if (direction === "left" || direction === "right") {
      setDesignIndex(index => clamp(index + (direction === "right" ? steps : -steps), versions.length));
    } else {
      setColorIndex(index => clamp(index + (direction === "down" ? steps : -steps), BLUEPRINT_HAT_COLORS.length));
    }
  }, [versions.length]);

  // Warm the active preview and the two nearest choices; schedule neighbors after
  // the active image so fast drag/key navigation can reuse decoded browser images.
  useEffect(() => {
    if (!activeDesign) return;
    prefetchBlueprintImage(blueprintHatPhoto(activeColor.id));
    prefetchBlueprintImage(BLUEPRINT_SIDE_ATLAS);
    prefetchBlueprintImage(thumbnailUrl(activeDesign.url, 512));
    const timer = window.setTimeout(() => {
      for (const offset of [-2, -1, 1, 2]) {
        const color = BLUEPRINT_HAT_COLORS[colorIndex + offset];
        if (color) {
          prefetchBlueprintImage(blueprintHatPhoto(color.id));

        }
        const design = versions[selectedDesign + offset];
        if (design) prefetchBlueprintImage(thumbnailUrl(design.url, 512));
      }
    }, 80);
    return () => window.clearTimeout(timer);
  }, [activeColor.id, activeDesign?.url, colorIndex, selectedDesign, versions]);

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
  }, [move]);

  const onPointerDown = (event: ReactPointerEvent<HTMLElement>, axis: DragAxis) => {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    if ((event.target as HTMLElement).closest("a,input")) return;
    dragOrigin.current = { x: event.clientX, y: event.clientY, pointerId: event.pointerId, axis };
    suppressNextClick.current = false;
  };
  const onPointerMove = (event: ReactPointerEvent<HTMLElement>) => {
    const start = dragOrigin.current;
    if (!start || start.pointerId !== event.pointerId) return;
    if (Math.hypot(event.clientX - start.x, event.clientY - start.y) >= 8) {
      event.currentTarget.setPointerCapture?.(event.pointerId);
    }
  };
  const onPointerUp = (event: ReactPointerEvent<HTMLElement>) => {
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
              <DeferredImage src={thumbnailUrl(design.url, 240)} rootMargin="180px" alt="" draggable={false} loading="lazy" decoding="async"/>
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
              <DeferredImage className="rrugc-blueprint-color-hat" src={blueprintHatPhoto(color.id)} rootMargin="180px"
                alt="" loading="lazy" decoding="async" draggable={false}/>
              <span className="rrugc-blueprint-color-name">{color.short}</span>
            </button>;
          })}
        </div>
        <button type="button" className="rrugc-blueprint-color-control is-next" aria-label="Next hat color"
          disabled={colorIndex === BLUEPRINT_HAT_COLORS.length - 1} onClick={() => move("down")}>↓</button>
      </div>

      <div className="rrugc-blueprint-preview-area" role="region" aria-label="Front and right-facing side previews for selected hat">
        <article className="rrugc-blueprint-selected-card" onPointerDown={event => onPointerDown(event,"both")}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          onPointerCancel={() => { dragOrigin.current = null; }}>
          <header className="rrugc-blueprint-panel-header">
            <div className="rrugc-blueprint-panel-heading">
              <strong>FRONT</strong>
              <small>Hoop Red WACE · Placement preview</small>
            </div>
            <div className="rrugc-blueprint-panel-header-actions">
              <button type="button" className="rrugc-blueprint-thread-swatch"
                title={"Palette: " + activePalette.name + " (" + ((activePaletteOrdinal % paletteCount) + 1) + "/" + paletteCount + ") · Click to try the next dark combination"}
                aria-label={"Next Front embroidery palette for " + activeColor.short}
                onClick={event => { event.stopPropagation(); cyclePalette(); }}
                onPointerDown={event => event.stopPropagation()}
                style={{ backgroundImage: paletteSwatch }}><span aria-hidden="true">↻</span></button>
              <BlueprintViewZoom label="Front" value={frontDesignSize} onChange={setFrontDesignSize}/>
            </div>
          </header>
          <div className="rrugc-blueprint-panel-visual">
            <div className="rrugc-blueprint-selected-hat"
              onPointerMove={event => moveBlueprintLens(event, frontLens.current,
                Array.from(event.currentTarget.querySelectorAll<HTMLImageElement>(".rrugc-blueprint-smooth-img")).every(image => image.classList.contains("is-loaded")))}
              onPointerLeave={() => hideBlueprintLens(frontLens.current)}
              onPointerDown={() => hideBlueprintLens(frontLens.current)}>
              <div className="rrugc-blueprint-front-scene">
                <BlueprintSmoothImage className="rrugc-blueprint-hat-original" src={blueprintHatPhoto(activeColor.id)}
                  alt={"Valucap 8869 original cap front, " + activeColor.label} draggable={false}/>
                <div ref={frontFit.frameRef} className="rrugc-blueprint-selected-design">
                  <BlueprintSmoothImage key={activeDesign.version} src={tintedFrontSrc}
                    onLoad={frontFit.onLoad}
                    style={frontFit.style ?? { opacity: 0 }}
                    alt={designName(activeDesign) + " fitted embroidery on " + activeColor.label} draggable={false}/>
                </div>
                {guides && <>
                  <div className="rrugc-blueprint-front-guide" aria-label="Front embroidery guideline">
                    <span>Front embroidery area</span>
                  </div>
                  <div className="rrugc-blueprint-front-height-guides" role="img" aria-label="Maximum height of front embroidery between two blue horizontal lines">
                    <span className="rrugc-blueprint-height-line is-upper"/>
                    <span className="rrugc-blueprint-height-line is-lower"/>
                  </div>
                </>}
              </div>
              <div ref={frontLens} className="rrugc-blueprint-magnifier" aria-hidden="true">
                <div className="rrugc-blueprint-magnifier-scene">
                  <div className="rrugc-blueprint-front-scene">
                    <img className="rrugc-blueprint-hat-original" src={blueprintHatPhoto(activeColor.id)} alt="" draggable={false}/>
                    <div className="rrugc-blueprint-selected-design">
                      <img src={tintedFrontSrc} alt="" draggable={false}
                        style={frontFit.style ?? { opacity: 0 }}/>
                    </div>
                    {guides && <>
                      <div className="rrugc-blueprint-front-guide"><span>Front embroidery area</span></div>
                      <div className="rrugc-blueprint-front-height-guides" aria-hidden="true">
                        <span className="rrugc-blueprint-height-line is-upper"/>
                        <span className="rrugc-blueprint-height-line is-lower"/>
                      </div>
                    </>}
                  </div>
                </div>
              </div>
            </div>
          </div>
          <footer className="rrugc-blueprint-panel-footer">
            <div className="rrugc-blueprint-selected-info" role="status" aria-live="polite">
              <strong title={designName(activeDesign)}>{designName(activeDesign)}</strong>
              <span>{activeColor.label}</span>
            </div>
            <a className="rrugc-blueprint-original" href={activeDesign.url} target="_blank" rel="noreferrer">View original design ↗</a>
          </footer>
        </article>
        <article className="rrugc-blueprint-side-card" aria-label={"Right-facing side preview of " + activeColor.label}
          onPointerDown={event => onPointerDown(event,"both")}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          onPointerCancel={() => { dragOrigin.current = null; }}>
          <header className="rrugc-blueprint-panel-header">
            <div className="rrugc-blueprint-panel-heading">
              <strong>RIGHT SIDE</strong>
              <small>Hoop Cap Clamp · Placement preview</small>
            </div>
            <div className="rrugc-blueprint-panel-header-actions">
              <span className="rrugc-blueprint-side-color">{activeColor.short}</span>
              <button type="button" className="rrugc-blueprint-thread-swatch"
                title={"Palette: " + activePalette.name + " (" + ((activePaletteOrdinal % paletteCount) + 1) + "/" + paletteCount + ") · Click to try the next dark combination"}
                aria-label={"Next Right side embroidery palette for " + activeColor.short}
                onClick={event => { event.stopPropagation(); cyclePalette(); }}
                onPointerDown={event => event.stopPropagation()}
                style={{ backgroundImage: paletteSwatch }}><span aria-hidden="true">↻</span></button>
              <BlueprintViewZoom label="Right side" value={sideDesignSize} onChange={setSideDesignSize}/>
            </div>
          </header>
          <div className="rrugc-blueprint-panel-visual">
            {sideAtlasFailed
              ? <div className="rrugc-blueprint-side-unavailable" role="status">Side reference image could not be loaded</div>
              : <div className={"rrugc-blueprint-side-image" + (sideAtlasReady ? " is-ready" : "")}
                  role="img" aria-label={"Valucap 8869 right-facing mirrored side reference, " + activeColor.label}
                  onPointerMove={event => moveBlueprintLens(event, sideLens.current, sideAtlasReady)}
                  onPointerLeave={() => hideBlueprintLens(sideLens.current)}
                  onPointerDown={() => hideBlueprintLens(sideLens.current)}>
                  <img className="rrugc-blueprint-side-probe" src={BLUEPRINT_SIDE_ATLAS} alt=""
                    aria-hidden="true" onLoad={() => setSideAtlasReady(true)}
                    onError={() => setSideAtlasFailed(true)} />
                  <div className="rrugc-blueprint-side-content" style={blueprintSidePosition(colorIndex)}>
                  </div>
                  <img className="rrugc-blueprint-side-calibration-design"
                    src={tintedSideSrc}
                    style={{ "--side-design-scale": sideDesignSize / 100 } as React.CSSProperties}
                    draggable={false} alt="Roberts reference embroidery, default right-side sizing design"/>
                  {guides && <BlueprintSideGuides />}
                  <div ref={sideLens} className="rrugc-blueprint-magnifier" aria-hidden="true">
                    <div className="rrugc-blueprint-magnifier-scene">
                      <div className="rrugc-blueprint-magnifier-side-surface">
                        <div className="rrugc-blueprint-side-content" style={blueprintSidePosition(colorIndex)}>
                        </div>
                        <img className="rrugc-blueprint-side-calibration-design"
                          src={tintedSideSrc}
                          style={{ "--side-design-scale": sideDesignSize / 100 } as React.CSSProperties}
                          draggable={false} alt=""/>
                        {guides && <BlueprintSideGuides decorative />}
                      </div>
                    </div>
                  </div>
                </div>}
          </div>
          <footer className="rrugc-blueprint-panel-footer">
            <span className="rrugc-blueprint-side-note">{activeColor.label}</span>
            <small>Roberts sizing reference · Right-side placement approximate</small>
          </footer>
        </article>
        <div className="rrugc-blueprint-nav-hint">← → Change design <span>·</span> ↑ ↓ Change hat color <span>·</span> Drag to browse</div>
      </div>
    </div>
    <div className="rrugc-blueprint-footnote">
      <span>Placement is approximate; verify embroidery proof before production.</span>
      <span>12 supplied 8869 references · No AI generation required</span>
    </div>
  </section>;
}