import { useCallback, useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import type { GenerationOutputVersion, HatStockCatalog } from "./api";
import { DeferredImage } from "./DeferredImage";
import { BlueprintSmoothImage, prefetchBlueprintImage } from "./BlueprintSmoothImage";
import { stage1OutputThumbnailUrl } from "./outputThumbnail";
import { hideBlueprintLens, moveBlueprintLens } from "./BlueprintMagnifier";
import { useBlueprintArtworkFit } from "./BlueprintDesignFit";
import { BlueprintViewZoom } from "./BlueprintViewZoom";
import { useBlueprintEmbroideryComposite } from "./embroidery/useBlueprintEmbroideryComposite";
import type { ThreadMode } from "./embroidery/EmbroideryMaterialCompositor";
import { BlueprintFrontDetailModal } from "./BlueprintFrontDetailModal";
import { FrontPlacementEditor } from "./FrontPlacementEditor";
import { DEFAULT_FRONT_PLACEMENT, frontPlacementChanged, frontPlacementStyle, type FrontPlacement } from "./FrontPlacement";
import "./BlueprintPreview.css";

/** Blueprint uses the SAME imported Valucap 8869 stock group for Front and Side. */
export const BLUEPRINT_HAT_COLORS = [
  { id: "black", key: "white-black", label: "White / Black", short: "Black" },
  { id: "brown", key: "natural-brown", label: "Natural / Brown", short: "Brown" },
  { id: "camo-green", key: "natural-camo-green", label: "Natural / Camo Green", short: "Camo Green" },
  { id: "charcoal", key: "natural-charcoal", label: "Natural / Charcoal", short: "Charcoal" },
  { id: "forest-green", key: "natural-forest-green", label: "Natural / Forest Green", short: "Forest Green" },
  { id: "khaki", key: "natural-khaki", label: "Natural / Khaki", short: "Khaki" },
  { id: "maroon", key: "natural-maroon", label: "Natural / Maroon", short: "Maroon" },
  { id: "mossy-oak-breakup", key: "natural-mossy-oak-breakup", label: "Natural / Mossy Oak", short: "Mossy Oak" },
  { id: "realtree-all-purpose", key: "natural-realtree-all-purpose", label: "Natural / Realtree", short: "Realtree" },
  { id: "royal", key: "natural-royal", label: "Natural / Royal", short: "Royal" },
  { id: "white-navy", key: "white-navy", label: "White / Navy", short: "White/Navy" },
  { id: "white-red", key: "white-red", label: "White / Red", short: "White/Red" },
] as const;

export function blueprintHatPhoto(
  colorId: string, catalog: HatStockCatalog | null = null, view: "front" | "side" = "front",
) {
  const color = BLUEPRINT_HAT_COLORS.find(item => item.id === colorId);
  if (!color) return "";
  const synced = catalog?.colors[color.key]?.views[view];
  if (typeof synced === "string" && synced.startsWith("/") && !synced.startsWith("//")) return synced;
  // Missing synchronized stock is NOT replaced by the legacy small JPEGs.
  return "";
}

export function blueprintAvailableColors(catalog: HatStockCatalog | null = null) {
  return BLUEPRINT_HAT_COLORS.filter(color =>
    Boolean(blueprintHatPhoto(color.id, catalog, "front") && blueprintHatPhoto(color.id, catalog, "side")));
}

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

function designName(version: GenerationOutputVersion) {
  return version.output_name?.split(/[\\/]/).filter(Boolean).pop() || "Design " + version.version;
}

type DragAxis = "horizontal" | "vertical" | "both";
type DragOrigin = { x: number; y: number; pointerId: number; axis: DragAxis };

/** Only near neighbors are mounted; the active thumbnail and hat stay physically centered. */
export function BlueprintPreview({ versions, initialVersion, onToggleStage2Selection, savingSelectionVersion, guides = false, threadMode = "auto",
  placement = DEFAULT_FRONT_PLACEMENT, placementReady = false, onSavePlacement, onSaveDesignSize, stockCatalog = null, stockCatalogLoading = false,
}: {
  versions: GenerationOutputVersion[];
  initialVersion?: number;
  onToggleStage2Selection?: (version: GenerationOutputVersion) => void;
  savingSelectionVersion?: number | null;
  guides?: boolean;
  threadMode?: ThreadMode;
  placement?: FrontPlacement;
  placementReady?: boolean;
  onSavePlacement?: (placement: FrontPlacement) => Promise<FrontPlacement>;
  onSaveDesignSize?: (version: number, percent: number) => Promise<number>;
  stockCatalog?: HatStockCatalog | null;
  stockCatalogLoading?: boolean;
}) {
  const initialIndex = initialVersion ? versions.findIndex(item => item.version === initialVersion) : 0;
  const [designIndex, setDesignIndex] = useState(Math.max(0, initialIndex));
  const [colorIndex, setColorIndex] = useState(0);
  const [frontDesignSize, setFrontDesignSize] = useState(100);
  const [frontDetailOpen, setFrontDetailOpen] = useState(false);
  const [editingPlacement, setEditingPlacement] = useState(false);
  const [draftPlacement, setDraftPlacement] = useState<FrontPlacement>(placement);
  const [placementSaving, setPlacementSaving] = useState(false);
  const [sizeSaving, setSizeSaving] = useState(false);
  const [placementMessage, setPlacementMessage] = useState("");
  const [placementError, setPlacementError] = useState("");
  const visiblePlacement = editingPlacement ? draftPlacement : placement;
  const placementStyle = frontPlacementStyle(visiblePlacement);
  const placementChanged = frontPlacementChanged(draftPlacement, placement);
  useEffect(() => {
    if (!editingPlacement) setDraftPlacement(placement);
  }, [editingPlacement, placement]);

  const beginPlacementEdit = () => {
    setDraftPlacement(placement);
    setPlacementMessage("");
    setPlacementError("");
    setEditingPlacement(true);
    hideBlueprintLens(frontLens.current);
  };
  const cancelPlacementEdit = () => {
    setDraftPlacement(placement);
    setFrontDesignSize(versions[Math.min(designIndex, versions.length - 1)]?.front_design_size ?? 100);
    setEditingPlacement(false);
    setPlacementMessage("");
    setPlacementError("");
  };
  const savePlacement = async () => {
    if (!onSavePlacement || (!placementChanged && !designSizeChanged) || placementSaving) return;
    setPlacementSaving(true);
    setPlacementError("");
    setPlacementMessage("");
    try {
      const result = placementChanged ? await onSavePlacement(draftPlacement) : placement;
      if (designSizeChanged && activeDesign && onSaveDesignSize) {
        await onSaveDesignSize(activeDesign.version, frontDesignSize);
      }
      setDraftPlacement(result);
      setEditingPlacement(false);
      setPlacementMessage("Front placement and design size saved.");
    } catch (cause) {
      setPlacementError(cause instanceof Error ? cause.message : "Could not save front placement.");
    } finally {
      setPlacementSaving(false);
    }
  };
  const saveDesignSize = async () => {
    if (!onSaveDesignSize || !activeDesign || !designSizeChanged || sizeSaving) return;
    setSizeSaving(true);
    setPlacementError("");
    setPlacementMessage("");
    try {
      await onSaveDesignSize(activeDesign.version, frontDesignSize);
      setPlacementMessage("Design size saved for this Stage 1 artwork.");
    } catch (cause) {
      setPlacementError(cause instanceof Error ? cause.message : "Could not save design size.");
    } finally {
      setSizeSaving(false);
    }
  };
  const [sideFailedSource, setSideFailedSource] = useState<string | null>(null);
  const [artworkFailedSource, setArtworkFailedSource] = useState<string | null>(null);
  const dragOrigin = useRef<DragOrigin | null>(null);
  const designTrackRef = useRef<HTMLDivElement>(null);
  const suppressNextClick = useRef(false);
  const frontLens = useRef<HTMLDivElement>(null);
  const sideLens = useRef<HTMLDivElement>(null);
  const selectedDesign = clamp(designIndex, versions.length);
  const activeDesign = versions[selectedDesign];
  const savedDesignSize = activeDesign?.front_design_size ?? 100;
  const designSizeChanged = frontDesignSize !== savedDesignSize;
  useEffect(() => {
    setFrontDesignSize(savedDesignSize);
  }, [activeDesign?.version, savedDesignSize]);
  const synchronizedColors = blueprintAvailableColors(stockCatalog);
  const selectedColorIndex = Math.min(colorIndex, synchronizedColors.length - 1);
  const activeColor = synchronizedColors[selectedColorIndex] ?? BLUEPRINT_HAT_COLORS[0];
  const frontHatSrc = blueprintHatPhoto(activeColor.id, stockCatalog, "front");
  const sideHatSrc = blueprintHatPhoto(activeColor.id, stockCatalog, "side");
  // Only the cap canvas requests ephemeral stitch geometry. The thumbnail,
  // original and downloaded Stage 1 design stay RGB embroidery-on-fabric.
  // Keep the routine cap compositor at preview resolution. Only the zoom
  // dialog requests full geometry, retaining high-frequency stitches on demand.
  const fullDesignSrc = activeDesign ? activeDesign.url
    + (activeDesign.url.includes("?") ? "&" : "?")
    + "artwork=stage1-thread-geometry-v1" : "";
  const activeDesignSrc = activeDesign ? activeDesign.url
    + (activeDesign.url.includes("?") ? "&" : "?")
    + "thumbnail=true&size=1024&artwork=stage1-thread-geometry-v1" : "";
  // Render actual stitch detail, not a 512px downsample.
  // Recolor each design by hat color while preserving stitch luminance and applying
  // the same low-profile material simulation in preview, lens and detail modal.
  const tintedFrontSrc = useBlueprintEmbroideryComposite(activeDesignSrc, frontHatSrc, activeColor.id, threadMode);
  const frontFit = useBlueprintArtworkFit(activeDesignSrc, frontDesignSize);
  // Do not hide embroidery while the fit observer measures the field. The
  // source endpoint is alpha-only, so its temporary contain-fit is safe.
  const fallbackArtworkStyle = {
    position: "absolute" as const, left: 0, top: 0, width: "100%", height: "100%",
    objectFit: "contain" as const,
  };

  useEffect(() => {
    hideBlueprintLens(frontLens.current);
    hideBlueprintLens(sideLens.current);
  }, [activeColor.id, activeDesign?.url, frontHatSrc, sideHatSrc]);

  const move = useCallback((direction: "left" | "right" | "up" | "down", steps = 1) => {
    if (direction === "left" || direction === "right") {
      setDesignIndex(index => clamp(index + (direction === "right" ? steps : -steps), versions.length));
    } else {
      setColorIndex(index => clamp(index + (direction === "down" ? steps : -steps), synchronizedColors.length));
    }
  }, [versions.length, synchronizedColors.length]);

  // Avoid speculative Drive work: the visible 224px cards already lazy-load
  // their own WebP thumbnails. Warm only one adjacent factory hat photograph
  // after the selected front/side have had a chance to finish.
  useEffect(() => {
    if (!activeDesign || !stockCatalog) return;
    const timer = window.setTimeout(() => {
      const next = synchronizedColors[selectedColorIndex + 1] ?? synchronizedColors[selectedColorIndex - 1];
      if (next) prefetchBlueprintImage(blueprintHatPhoto(next.id, stockCatalog, "front"));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [activeDesign?.version, selectedColorIndex, stockCatalog]);

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
    if ((event.target as HTMLElement).closest("a,input,[data-blueprint-design-action]")) return;
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

  // Keep the complete design list left-aligned. Selecting an offscreen item
  // moves the strip only enough to reveal it, never centers the active card.
  useEffect(() => {
    const track = designTrackRef.current;
    const card = track?.querySelector<HTMLElement>('[data-design-index="' + selectedDesign + '"]');
    if (!track || !card || !track.clientWidth) return;
    const left = card.offsetLeft;
    const right = left + card.offsetWidth;
    if (left < track.scrollLeft) track.scrollLeft = left;
    else if (right > track.scrollLeft + track.clientWidth) {
      track.scrollLeft = right - track.clientWidth;
    }
  }, [selectedDesign, versions]);

  const neighbors = [-3, -2, -1, 0, 1, 2, 3];
  if (!activeDesign) return <div className="rrugc-blueprint-empty">No designs available to preview.</div>;
  if (!synchronizedColors.length) return <div className="rrugc-blueprint-empty" role="status">
    {stockCatalogLoading
      ? "Loading synchronized Valucap 8869 stock photos…"
      : "No synchronized Valucap 8869 Front/Side stock pair is available. Open Stage 2 and Sync Hat Images."}
  </div>;

  return <section className="rrugc-blueprint rrugc-blueprint-cross" aria-label="Blueprint cross puzzle · design and hat color navigator">
    <div className="rrugc-blueprint-cross-layout">
      <div className="rrugc-blueprint-design-carousel" aria-label="Designs carousel">
        <button type="button" className="rrugc-blueprint-design-control is-prev"
          aria-label="Previous design columns" disabled={selectedDesign === 0} onClick={() => move("left")}>‹</button>
        <div ref={designTrackRef} className="rrugc-blueprint-design-track" role="group"
          aria-label="Drag horizontally to select designs" onPointerDown={event => onPointerDown(event,"horizontal")}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          onPointerCancel={() => { dragOrigin.current = null; }} onClickCapture={clickCapture}>
          {versions.map((design, index) => {
            const isActive = index === selectedDesign;
            const starred = Boolean(design.selected_for_products_stage2);
            const validPng = (!design.output_name || design.output_name.toLowerCase().endsWith(".png"));
            return <div key={design.version}
              data-design-index={index}
              className={"rrugc-blueprint-design-item" + (isActive ? " is-active" : "") + (starred ? " is-starred" : "")}>
              <button type="button"
                className={"rrugc-blueprint-design-choice" + (isActive ? " is-selected" : "")}
                aria-pressed={isActive} aria-label={"Select design " + designName(design)}
                onClick={() => setDesignIndex(index)}>
                <span className="rrugc-blueprint-design-caption">DESIGN {index + 1}{isActive ? " · ACTIVE" : ""}</span>
                <DeferredImage src={stage1OutputThumbnailUrl(design.url, 224)} rootMargin="100px"
                  alt="" draggable={false} loading="lazy" decoding="async"/>
                <strong title={designName(design)}>{designName(design)}</strong>
              </button>
              {onToggleStage2Selection && <button type="button"
                data-blueprint-design-action="star"
                className={"rrugc-blueprint-design-star" + (starred ? " is-starred" : "")}
                aria-label={(starred ? "Remove design from Products Stage 2: " : "Star design for Products Stage 2: ") + designName(design)}
                title={starred ? "Remove from Products Stage 2" : "Select for Products Stage 2"}
                aria-pressed={starred}
                disabled={!validPng || savingSelectionVersion != null}
                onPointerDown={event => event.stopPropagation()}
                onClick={event => { event.stopPropagation(); onToggleStage2Selection(design); }}>
                <svg viewBox="0 0 24 24" aria-hidden="true" fill={starred ? "currentColor" : "none"} stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round">
                  <path d="m12 2.5 2.95 6 6.6.96-4.77 4.65 1.13 6.57L12 17.57l-5.91 3.11 1.13-6.57-4.77-4.65 6.6-.96Z" />
                </svg>
              </button>}
              <a data-blueprint-design-action="download" className="rrugc-blueprint-design-download"
                href={design.url + (design.url.includes("?") ? "&" : "?") + "download=true"}
                download aria-label={"Download design " + designName(design)} title="Download original design"
                onPointerDown={event => event.stopPropagation()} onClick={event => event.stopPropagation()}>
                <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3v12m-4-4 4 4 4-4M5 17v3h14v-3"/>
                </svg>
              </a>
            </div>;
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
            const index = selectedColorIndex + delta;
            const color = synchronizedColors[index];
            if (!color) return null;
            return <button type="button" key={color.id}
              className={"rrugc-blueprint-color-choice" + (delta === 0 ? " is-selected" : "")}
              style={{ transform: "translate(-50%, calc(-50% + " + (delta * 94) + "px))" }}
              aria-label={"Select hat color " + color.label} aria-pressed={delta === 0}
              onClick={() => setColorIndex(index)}>
              <DeferredImage className="rrugc-blueprint-color-hat" src={blueprintHatPhoto(color.id, stockCatalog, "front")} rootMargin="180px"
                alt="" loading="lazy" decoding="async" draggable={false}/>
              <span className="rrugc-blueprint-color-name">{color.short}</span>
            </button>;
          })}
        </div>
        <button type="button" className="rrugc-blueprint-color-control is-next" aria-label="Next hat color"
          disabled={selectedColorIndex === synchronizedColors.length - 1} onClick={() => move("down")}>↓</button>
      </div>

      <div className="rrugc-blueprint-preview-area" role="region" aria-label="Front and right-facing side previews for selected hat">
        <article className="rrugc-blueprint-selected-card" onPointerDown={event => onPointerDown(event,"both")}
          onPointerMove={onPointerMove} onPointerUp={onPointerUp}
          onPointerCancel={() => { dragOrigin.current = null; }}>
          <div className="rrugc-blueprint-selected-info rrugc-blueprint-selection-a11y" role="status" aria-live="polite">
            <strong>{designName(activeDesign)}</strong>
            <span>{activeColor.label}</span>
          </div>
          <header className="rrugc-blueprint-panel-header">
            <div className="rrugc-blueprint-panel-heading">
              <strong>FRONT</strong>
              <small>Hoop Red WACE · Placement preview</small>
            </div>
            <div className="rrugc-blueprint-panel-header-actions">
              {onSavePlacement && <div className="rrugc-blueprint-front-placement-actions" role="group" aria-label="Front placement settings">
                {editingPlacement ? <>
                  <button type="button" className="is-primary" disabled={placementSaving || (!placementChanged && !designSizeChanged)} onClick={() => void savePlacement()}> {placementSaving ? "Saving…" : "Save"} </button>
                  <button type="button" disabled={placementSaving} onClick={cancelPlacementEdit}>Cancel</button>
                  <button type="button" disabled={placementSaving} title="Restore factory placement (save to apply)" onClick={() => setDraftPlacement({
                    ...DEFAULT_FRONT_PLACEMENT, revision: placement.revision,
                  })}>Reset</button>
                </> : <button type="button" disabled={!placementReady} onClick={beginPlacementEdit}
                  title="Move or resize the front embroidery field and blue height lines">Edit placement</button>}
              </div>}
              <span className="rrugc-blueprint-thread-source" title="Thread material is rendered on cap with preserved stitch highlights">{threadMode === "auto" ? "Auto-match thread" : "Original threads"}</span>
              <BlueprintViewZoom label="Front" value={frontDesignSize} onChange={setFrontDesignSize}/>
              {!editingPlacement && onSaveDesignSize && designSizeChanged && <button type="button"
                className="rrugc-blueprint-size-save" disabled={sizeSaving || !placementReady}
                onClick={() => void saveDesignSize()}>{sizeSaving ? "Saving…" : "Save size"}</button>}
            </div>
          </header>
          {editingPlacement && <p className="rrugc-blueprint-placement-feedback is-hint">Drag the cyan area to move · use its handles to resize · drag each blue line independently.</p>}
          {placementError && <p className="rrugc-blueprint-placement-feedback is-error" role="alert">{placementError}</p>}
          {placementMessage && !editingPlacement && <p className="rrugc-blueprint-placement-feedback is-saved" role="status">{placementMessage}</p>}
          <div className="rrugc-blueprint-panel-visual">
            <div className="rrugc-blueprint-selected-hat rrugc-blueprint-front-open-detail" role="button" tabIndex={0}
              aria-label={"Enlarge front embroidery: " + designName(activeDesign) + " on " + activeColor.label}
              title="Click to enlarge and inspect embroidery details"
              onClick={() => { if (!editingPlacement && !suppressNextClick.current) { hideBlueprintLens(frontLens.current); setFrontDetailOpen(true); } }}
              onKeyDown={event => { if (!editingPlacement && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); event.stopPropagation(); setFrontDetailOpen(true); } }}
              onPointerMove={event => !editingPlacement && moveBlueprintLens(event, frontLens.current,
                Boolean(event.currentTarget.querySelector(".rrugc-blueprint-hat-original.is-loaded")))}
              onPointerLeave={() => hideBlueprintLens(frontLens.current)}
              onPointerDown={() => hideBlueprintLens(frontLens.current)}>
              <div className="rrugc-blueprint-front-scene" style={placementStyle}>
                <BlueprintSmoothImage className="rrugc-blueprint-hat-original" src={frontHatSrc}
                  alt={"Valucap 8869 original cap front, " + activeColor.label} draggable={false}/>
                <div ref={frontFit.frameRef} className="rrugc-blueprint-selected-design">
                  <BlueprintSmoothImage key={activeDesign.version} src={tintedFrontSrc}
                    onLoad={event => { frontFit.onLoad(event); setArtworkFailedSource(null); }}
                    onError={() => setArtworkFailedSource(activeDesignSrc)}
                    style={frontFit.style ?? fallbackArtworkStyle}
                    alt={designName(activeDesign) + " fitted embroidery on " + activeColor.label} draggable={false}/>
                  {guides && !editingPlacement && frontFit.guideStyle &&
                    <div className="rrugc-blueprint-front-guide is-artwork-fit" style={frontFit.guideStyle}
                      aria-label="Visible embroidery footprint within the front placement field">
                      <span>Front embroidery area</span>
                    </div>}
                </div>
                {editingPlacement && <FrontPlacementEditor value={draftPlacement} onChange={setDraftPlacement}/>}
                {(guides && !editingPlacement) && <>
                  {!frontFit.guideStyle && <div className="rrugc-blueprint-front-guide" aria-label="Front embroidery guideline">
                    <span>Front embroidery area</span>
                  </div>}
                  <div className="rrugc-blueprint-front-height-guides" role="img" aria-label="Maximum height of front embroidery between two blue horizontal lines">
                    <span className="rrugc-blueprint-height-line is-upper"/>
                    <span className="rrugc-blueprint-height-line is-lower"/>
                  </div>
                </>}
              </div>
              {artworkFailedSource === activeDesignSrc && <span className="rrugc-blueprint-artwork-unavailable" role="status">
                This design has no usable transparent embroidery preview
              </span>}
              <span className="rrugc-blueprint-detail-affordance" aria-hidden="true">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.4 15.4 5 5M8 10.5h5m-2.5-2.5v5"/>
                </svg> Zoom
              </span>
              <div ref={frontLens} className="rrugc-blueprint-magnifier" aria-hidden="true">
                <div className="rrugc-blueprint-magnifier-scene">
                  <div className="rrugc-blueprint-front-scene" style={placementStyle}>
                    <img className="rrugc-blueprint-hat-original" src={frontHatSrc} alt="" draggable={false}/>
                    <div className="rrugc-blueprint-selected-design">
                      <img src={tintedFrontSrc} alt="" draggable={false}
                        style={frontFit.style ?? fallbackArtworkStyle}/>
                      {guides && frontFit.guideStyle &&
                        <div className="rrugc-blueprint-front-guide is-artwork-fit" style={frontFit.guideStyle}>
                          <span>Front embroidery area</span>
                        </div>}
                    </div>
                    {guides && <>
                      {!frontFit.guideStyle && <div className="rrugc-blueprint-front-guide"><span>Front embroidery area</span></div>}
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
              <span className="rrugc-blueprint-thread-source" title="Official selected-color Valucap 8869 factory photo">Verified stock</span>
            </div>
          </header>
          <div className="rrugc-blueprint-panel-visual">
            {!sideHatSrc || sideFailedSource === sideHatSrc
              ? <div className="rrugc-blueprint-side-unavailable" role="status">
                  Official 8869 side stock unavailable for {activeColor.label}
                </div>
              : <div className="rrugc-blueprint-side-image is-stock is-ready" role="img"
                  aria-label={"Original Valucap 8869 side stock, " + activeColor.label}
                  onPointerMove={event => moveBlueprintLens(event, sideLens.current,
                    Boolean(event.currentTarget.querySelector(".rrugc-blueprint-side-hat.is-loaded")))}
                  onPointerLeave={() => hideBlueprintLens(sideLens.current)}
                  onPointerDown={() => hideBlueprintLens(sideLens.current)}>
                  <BlueprintSmoothImage className="rrugc-blueprint-side-hat" src={sideHatSrc}
                    alt={"Official Valucap 8869 side, " + activeColor.label}
                    onError={() => setSideFailedSource(sideHatSrc)} draggable={false}/>
                  {guides && <span className="rrugc-blueprint-side-guide-note">
                    Stock geometry preserved · prior atlas hoop calibration not applied
                  </span>}
                  <span className="rrugc-blueprint-detail-affordance" aria-hidden="true">
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                      <circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.4 15.4 5 5M8 10.5h5m-2.5-2.5v5"/>
                    </svg> Hover to zoom
                  </span>
                  <div ref={sideLens} className="rrugc-blueprint-magnifier" aria-hidden="true">
                    <div className="rrugc-blueprint-magnifier-scene">
                      <img className="rrugc-blueprint-side-hat" src={sideHatSrc} alt="" draggable={false}/>
                    </div>
                  </div>
                </div>}
          </div>
        </article>
        <div className="rrugc-blueprint-nav-hint">← → Change design <span>·</span> ↑ ↓ Change hat color <span>·</span> Drag to browse</div>
      </div>
    </div>
    <div className="rrugc-blueprint-footnote">
      <span>Placement is approximate; verify embroidery proof before production.</span>
      <span>{synchronizedColors.length} synchronized 8869 colorways · Verified Front/Side stock pairs · Thread color by hat</span>
    </div>
    {frontDetailOpen && <BlueprintFrontDetailModal
      hatSrc={frontHatSrc} designSrc={fullDesignSrc} compositedSrc={tintedFrontSrc}
      threadMode={threadMode} colorId={activeColor.id} colorLabel={activeColor.label}
      designLabel={designName(activeDesign)} designSize={frontDesignSize}
      guides={guides} placement={placement}
      onClose={() => setFrontDetailOpen(false)}
    />}
  </section>;
}