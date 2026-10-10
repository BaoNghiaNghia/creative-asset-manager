import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { createPortal } from "react-dom";
import { BlueprintSmoothImage } from "./BlueprintSmoothImage";
import { useBlueprintArtworkFit } from "./BlueprintDesignFit";
import { useBlueprintEmbroideryTint } from "./BlueprintEmbroideryTint";
import "./BlueprintFrontDetailModal.css";

type FrontDetailProps = {
  hatSrc: string;
  designSrc: string;
  colorId: string;
  colorLabel: string;
  designLabel: string;
  designSize: number;
  paletteOrdinal: number;
  guides: boolean;
  onClose: () => void;
};

/** Inspect the actual cap + fitted embroidery, rather than enlarging a 512px thumbnail. */
export function BlueprintFrontDetailModal({
  hatSrc, designSrc, colorId, colorLabel, designLabel, designSize, paletteOrdinal, guides, onClose,
}: FrontDetailProps) {
  const [zoom, setZoom] = useState(100);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragStart = useRef<{ id: number; x: number; y: number; originX: number; originY: number } | null>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const fullQualityEmbroidery = useBlueprintEmbroideryTint(designSrc, colorId, paletteOrdinal);
  const fit = useBlueprintArtworkFit(designSrc, designSize);

  useEffect(() => {
    const previouslyFocused = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeButtonRef.current?.focus();
    // The parent version-history dialog listens to Escape on window. Capture
    // first to close only the detail view, not both stacked dialogs.
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onCloseRef.current();
      } else if (event.key === "Tab") {
        const modal = document.querySelector<HTMLElement>(".rrugc-blueprint-detail-dialog");
        const controls = Array.from(modal?.querySelectorAll<HTMLElement>("button:not(:disabled),input:not(:disabled),a[href]") ?? []);
        if (controls.length === 0) return;
        const current = controls.indexOf(document.activeElement as HTMLElement);
        if (event.shiftKey && current <= 0) {
          event.preventDefault();
          controls[controls.length - 1].focus();
        } else if (!event.shiftKey && current === controls.length - 1) {
          event.preventDefault();
          controls[0].focus();
        }
      }
    };
    window.addEventListener("keydown", keydown, true);
    return () => {
      window.removeEventListener("keydown", keydown, true);
      previouslyFocused?.focus();
    };
  }, []);

  const changeZoom = (next: number) => {
    setZoom(Math.min(400, Math.max(100, next)));
    setOffset({ x: 0, y: 0 });
  };
  const down = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (zoom <= 100 || (event.pointerType === "mouse" && event.button !== 0)) return;
    dragStart.current = { id: event.pointerId, x: event.clientX, y: event.clientY, originX: offset.x, originY: offset.y };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const move = (event: ReactPointerEvent<HTMLDivElement>) => {
    const drag = dragStart.current;
    if (!drag || drag.id !== event.pointerId) return;
    const maxX = event.currentTarget.clientWidth * ((zoom / 100) - 1) / 2;
    const maxY = event.currentTarget.clientHeight * ((zoom / 100) - 1) / 2;
    setOffset({
      x: Math.max(-maxX, Math.min(maxX, drag.originX + event.clientX - drag.x)),
      y: Math.max(-maxY, Math.min(maxY, drag.originY + event.clientY - drag.y)),
    });
  };
  const up = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragStart.current?.id !== event.pointerId) return;
    dragStart.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) event.currentTarget.releasePointerCapture?.(event.pointerId);
  };

  return createPortal(
    <div className="rrugc-blueprint-detail-backdrop" role="presentation"
      onPointerDown={event => { if (event.target === event.currentTarget) { event.stopPropagation(); onClose(); } }}>
      <section className="rrugc-blueprint-detail-dialog" role="dialog" aria-modal="true"
        aria-label={"Front embroidery detail: " + designLabel + " on " + colorLabel}>
        <header className="rrugc-blueprint-detail-header">
          <div>
            <strong>Front embroidery detail</strong>
            <span>{designLabel} · {colorLabel}</span>
          </div>
          <button ref={closeButtonRef} type="button" className="rrugc-blueprint-detail-close"
            aria-label="Close front embroidery detail" onClick={onClose}>×</button>
        </header>
        <div className="rrugc-blueprint-detail-viewport"
          aria-label="Zoomed cap front, drag to inspect stitches when enlarged"
          onPointerDown={down} onPointerMove={move} onPointerUp={up}
          onPointerCancel={up}
          style={{ cursor: zoom > 100 ? "grab" : "default" }}>
          <div className="rrugc-blueprint-detail-pan" style={{
            transform: `translate(${offset.x}px, ${offset.y}px) scale(${zoom / 100})`,
          }}>
            <div className="rrugc-blueprint-detail-image">
              <div className="rrugc-blueprint-front-scene">
                <BlueprintSmoothImage className="rrugc-blueprint-hat-original" src={hatSrc}
                  alt={"Original Valucap 8869 hat, " + colorLabel} draggable={false}/>
                <div ref={fit.frameRef} className="rrugc-blueprint-selected-design">
                  <BlueprintSmoothImage src={fullQualityEmbroidery} key={designSrc + colorId + paletteOrdinal}
                    onLoad={fit.onLoad} style={fit.style ?? { opacity: 0 }}
                    alt={designLabel + " embroidery mounted on the front"} draggable={false}/>
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
        <footer className="rrugc-blueprint-detail-footer">
          <div className="rrugc-blueprint-detail-zoom-controls" role="group" aria-label="Front detail zoom controls">
            <button type="button" aria-label="Zoom out front detail"
              disabled={zoom === 100} onClick={() => changeZoom(zoom - 25)}>−</button>
            <input type="range" min="100" max="400" step="25" value={zoom}
              aria-label="Front embroidery detail zoom"
              onChange={event => changeZoom(Number(event.target.value))}/>
            <button type="button" aria-label="Zoom in front detail"
              disabled={zoom === 400} onClick={() => changeZoom(zoom + 25)}>+</button>
            <output aria-live="off">{zoom}%</output>
            <button type="button" className="rrugc-blueprint-detail-reset" disabled={zoom === 100}
              onClick={() => changeZoom(100)}>Fit</button>
          </div>
          <span>Zoom in and drag to inspect thread details</span>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
