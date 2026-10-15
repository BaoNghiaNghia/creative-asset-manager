import { useEffect, useRef, useState, type ReactNode } from "react";
import "./ResizableSourceLayout.css";

const MIN_WIDTH = 205;
const MAX_WIDTH = 475;
const DEFAULT_WIDTH = 295;

function clamp(value: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, value));
}

/** Shared accessible horizontal splitter for Reviews Stages 3/4/5. */
export function ResizableSourceLayout({
  stage, tree, children,
}: { stage: 3 | 4 | 5; tree: ReactNode; children: ReactNode }) {
  const [width, setWidth] = useState(() => {
    try {
      const saved = Number(window.localStorage.getItem("rrugc:source-tree-width:" + stage));
      return saved >= MIN_WIDTH && saved <= MAX_WIDTH ? saved : DEFAULT_WIDTH;
    } catch { return DEFAULT_WIDTH; }
  });
  const container = useRef<HTMLDivElement>(null);
  const drag = useRef<{ startX: number; startWidth: number } | null>(null);

  useEffect(() => {
    return () => { document.body.classList.remove("rrugc-source-split-dragging"); };
  }, []);

  function update(next: number) {
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
    event.currentTarget.setPointerCapture(event.pointerId);
    document.body.classList.add("rrugc-source-split-dragging");
    event.preventDefault();
  }
  function pointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    update(drag.current.startWidth + event.clientX - drag.current.startX);
  }
  function pointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId))
      event.currentTarget.releasePointerCapture(event.pointerId);
    finish();
  }
  useEffect(() => {
    try { window.localStorage.setItem("rrugc:source-tree-width:" + stage, String(width)); }
    catch { /* Storage can be unavailable in private browsing. */ }
  }, [stage, width]);

  return <div ref={container} className="rrugc-stage5-content-grid rrugc-resizable-source-layout"
    data-stage={stage} style={{ "--rrugc-source-tree-width": width + "px" } as React.CSSProperties}>
    <div className="rrugc-resizable-source-tree-pane">{tree}</div>
    <div className="rrugc-source-tree-splitter" role="separator" tabIndex={0}
      aria-label={`Resize Stage ${stage} source tree`}
      aria-orientation="vertical" aria-valuemin={MIN_WIDTH} aria-valuemax={MAX_WIDTH}
      aria-valuenow={width} title="Drag to resize source tree · Arrow keys to adjust"
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerUp}
      onLostPointerCapture={finish}
      onKeyDown={event => {
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault(); update(width + (event.key === "ArrowLeft" ? -20 : 20));
        } else if (event.key === "Home" || event.key === "End") {
          event.preventDefault(); update(event.key === "Home" ? MIN_WIDTH : MAX_WIDTH);
        }
      }}>
      <span aria-hidden="true" />
    </div>
    <div className="rrugc-stage5-folder-content rrugc-resizable-source-main">{children}</div>
  </div>;
}
