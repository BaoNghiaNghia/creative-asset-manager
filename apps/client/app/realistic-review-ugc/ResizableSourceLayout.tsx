import { useEffect, useRef, useState, type ReactNode } from "react";
import "./ResizableSourceLayout.css";

const MIN_WIDTH = 205;
const MAX_WIDTH = 475;
const DEFAULT_WIDTH = 295;
const MIN_PRODUCT_PERCENT = 20;
const MAX_PRODUCT_PERCENT = 45;
const DEFAULT_PRODUCT_PERCENT = 25;

function clamp(value: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, value));
}

function clampProductPercent(value: number): number {
  return Math.min(MAX_PRODUCT_PERCENT, Math.max(MIN_PRODUCT_PERCENT, value));
}

/** Shared accessible horizontal splitter for Products Stage 2 and Reviews 3/4/5. */
export function ResizableSourceLayout({
  stage, tree, children,
}: { stage: 2 | 3 | 4 | 5; tree: ReactNode; children: ReactNode }) {
  const isProductStage2 = stage === 2;
  const minWidth = isProductStage2 ? MIN_PRODUCT_PERCENT : MIN_WIDTH;
  const maxWidth = isProductStage2 ? MAX_PRODUCT_PERCENT : MAX_WIDTH;
  const defaultWidth = isProductStage2 ? DEFAULT_PRODUCT_PERCENT : DEFAULT_WIDTH;
  const [width, setWidth] = useState(() => {
    try {
      const saved = Number(window.localStorage.getItem("rrugc:source-tree-width:" + stage));
      return Number.isFinite(saved) && saved >= minWidth && saved <= maxWidth ? saved : defaultWidth;
    } catch { return defaultWidth; }
  });
  const container = useRef<HTMLDivElement>(null);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    return () => { document.body.classList.remove("rrugc-source-split-dragging"); };
  }, []);

  function update(next: number) {
    if (isProductStage2) {
      setWidth(clampProductPercent(next));
      return;
    }
    const measured = container.current?.getBoundingClientRect().width ?? 0;
    // JSDOM/hidden tab reports zero. In visible split mode reserve room for the grid.
    const containerWidth = measured > 560 ? measured : 1200;
    setWidth(clamp(Math.min(next, Math.max(MIN_WIDTH, containerWidth - 290))));
  }
  function finish() {
    drag.current = null;
    document.body.classList.remove("rrugc-source-split-dragging");
  }
  function pointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    drag.current = { startX: event.clientX, startWidth: width };
    event.currentTarget.setPointerCapture?.(event.pointerId);
    document.body.classList.add("rrugc-source-split-dragging");
    event.preventDefault();
  }
  function pointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    const containerWidth = container.current?.getBoundingClientRect().width || 1200;
    const delta = event.clientX - drag.current.startX;
    update(drag.current.startWidth + (isProductStage2 ? delta / containerWidth * 100 : delta));
  }
  function pointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId))
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    finish();
  }
  useEffect(() => {
    try { window.localStorage.setItem("rrugc:source-tree-width:" + stage, String(width)); }
    catch { /* Storage can be unavailable in private browsing. */ }
  }, [stage, width]);

  return <div ref={container} className="rrugc-stage5-content-grid rrugc-resizable-source-layout"
    data-stage={stage} style={{ "--rrugc-source-tree-width": width + (isProductStage2 ? "%" : "px") } as React.CSSProperties}>
    <div className="rrugc-resizable-source-tree-pane">{tree}</div>
    <div className="rrugc-source-tree-splitter" role="separator" tabIndex={0}
      aria-label={`Resize Stage ${stage} source tree`}
      aria-orientation="vertical" aria-valuemin={minWidth} aria-valuemax={maxWidth}
      aria-valuenow={width} title="Drag to resize source tree · Arrow keys to adjust"
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
      onLostPointerCapture={finish}
      onKeyDown={event => {
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault(); update(width + (event.key === "ArrowLeft" ? - (isProductStage2 ? 2 : 20) : (isProductStage2 ? 2 : 20)));
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault(); update(event.key === "Home" ? minWidth : maxWidth);
        }
      }}>
      <span aria-hidden="true" />
    </div>
    <div className="rrugc-stage5-folder-content rrugc-resizable-source-main">{children}</div>
  </div>;
}
