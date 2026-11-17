import {
  useEffect, useRef, useState,
  type CSSProperties, type PointerEvent as ReactPointerEvent,
} from "react";
import { DesignStageImage } from "./DesignStageImage";
import "./DesignZoomStage.css";

type Zoom = { scale: number; x: number; y: number };
const INITIAL_ZOOM: Zoom = { scale: 1, x: 0, y: 0 };
const MAX_ZOOM = 8;
const MIN_ZOOM = 1;

function clampOffset(value: number, scale: number, dimension: number) {
  // Don't allow the artwork's transformed viewport to float off the stage.
  return Math.max(dimension * (1 - scale), Math.min(0, value));
}

export function DesignZoomStage({
  originalUrl, thumbnailUrl, alt, positionLabel, variantLabel,
}: {
  originalUrl: string;
  thumbnailUrl: string;
  alt: string;
  positionLabel: string;
  variantLabel: string;
}) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<{ pointerId: number; x: number; y: number } | null>(null);
  const [zoom, setZoom] = useState<Zoom>(INITIAL_ZOOM);
  const [dragging, setDragging] = useState(false);

  // A native, non-passive wheel listener is essential: the browser must not
  // scroll its page/modal while the wheel is zooming artwork at the cursor.
  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = stage.getBoundingClientRect();
      if (rect.width < 1 || rect.height < 1) return;
      const modeFactor = event.deltaMode === WheelEvent.DOM_DELTA_LINE ? 16 :
        event.deltaMode === WheelEvent.DOM_DELTA_PAGE ? rect.height : 1;
      const delta = Math.max(-240, Math.min(240, event.deltaY * modeFactor));
      if (!Number.isFinite(delta) || delta === 0) return;
      const pointX = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
      const pointY = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
      setZoom(previous => {
        const scale = Math.max(MIN_ZOOM,
          Math.min(MAX_ZOOM, previous.scale * Math.exp(-delta * 0.002)));
        if (Math.abs(scale - 1) < 0.001) return INITIAL_ZOOM;
        if (scale === previous.scale) return previous;
        const ratio = scale / previous.scale;
        // Keep the image point under the cursor stationary:
        // localPoint = (cursor - oldTranslation) / oldScale.
        return {
          scale,
          x: clampOffset(pointX - (pointX - previous.x) * ratio, scale, rect.width),
          y: clampOffset(pointY - (pointY - previous.y) * ratio, scale, rect.height),
        };
      });
    };
    stage.addEventListener("wheel", onWheel, { passive: false });
    return () => stage.removeEventListener("wheel", onWheel);
  }, []);

  const reset = () => {
    dragRef.current = null;
    setDragging(false);
    setZoom(INITIAL_ZOOM);
  };
  const stopDragging = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (dragRef.current?.pointerId !== event.pointerId) return;
    dragRef.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  return (
    <div ref={stageRef}
      className={"etsy-design-gallery-stage etsy-design-gallery-zoom-stage" +
        (zoom.scale > 1 ? " is-zoomed" : "") + (dragging ? " is-panning" : "")}
      aria-label="Ảnh thiết kế xem trước: lăn bánh xe để phóng to hoặc thu nhỏ, nhấn bánh xe giữa để đặt lại"
      title="Lăn bánh xe để zoom tại con trỏ · Nhấn bánh xe giữa để đặt lại · Kéo để di chuyển khi phóng to"
      data-zoom-scale={zoom.scale.toFixed(3)}
      style={{
        "--etsy-preview-scale": zoom.scale,
        "--etsy-preview-x": `${zoom.x}px`,
        "--etsy-preview-y": `${zoom.y}px`,
        touchAction: zoom.scale > 1 ? "none" : "auto",
      } as CSSProperties}
      onMouseDown={event => {
        if (event.button === 1) {
          event.preventDefault(); // disable browser middle-button autoscroll
          reset();
        }
      }}
      onAuxClick={event => {
        if (event.button === 1) {
          event.preventDefault();
          reset();
        }
      }}
      onPointerDown={event => {
        if (event.button !== 0 || zoom.scale <= 1) return;
        if (event.target instanceof Element && event.target.closest("button")) return;
        event.preventDefault();
        dragRef.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY };
        setDragging(true);
        event.currentTarget.setPointerCapture?.(event.pointerId);
      }}
      onPointerMove={event => {
        const drag = dragRef.current;
        if (!drag || drag.pointerId !== event.pointerId) return;
        const dx = event.clientX - drag.x;
        const dy = event.clientY - drag.y;
        drag.x = event.clientX;
        drag.y = event.clientY;
        const rect = event.currentTarget.getBoundingClientRect();
        setZoom(previous => ({
          ...previous,
          x: clampOffset(previous.x + dx, previous.scale, rect.width),
          y: clampOffset(previous.y + dy, previous.scale, rect.height),
        }));
      }}
      onPointerUp={stopDragging}
      onPointerCancel={stopDragging}
      onLostPointerCapture={() => { dragRef.current = null; setDragging(false); }}
      onDragStart={event => event.preventDefault()}>
      <span className="etsy-design-gallery-stage-chip">
        {positionLabel} <span aria-hidden="true">/</span> {variantLabel}
      </span>
      <DesignStageImage originalUrl={originalUrl} thumbnailUrl={thumbnailUrl} alt={alt} />
      {zoom.scale > 1.001 && (
        <span className="etsy-design-gallery-zoom-status" aria-live="off">
          {Math.round(zoom.scale * 100)}% · Nhấn bánh xe giữa để đặt lại
        </span>
      )}
    </div>
  );
}
