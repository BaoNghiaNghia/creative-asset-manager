import { useRef, useState } from "react";
import type { MouseEventHandler, PointerEventHandler } from "react";

const DRAG_THRESHOLD_PX = 5;

type DragState = {
  pointerId: number;
  startX: number;
  startScrollLeft: number;
  moved: boolean;
};

export function useHorizontalDragScroll() {
  const dragRef = useRef<DragState | null>(null);
  const suppressClickRef = useRef(false);
  const [dragging, setDragging] = useState(false);

  const onPointerDown: PointerEventHandler<HTMLDivElement> = event => {
    if (event.button !== 0 || event.pointerType === "touch") return;
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startScrollLeft: event.currentTarget.scrollLeft,
      moved: false,
    };
  };

  const onPointerMove: PointerEventHandler<HTMLDivElement> = event => {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;

    const delta = event.clientX - active.startX;
    if (!active.moved && Math.abs(delta) < DRAG_THRESHOLD_PX) return;

    if (!active.moved) {
      active.moved = true;
      suppressClickRef.current = true;
      if (typeof event.currentTarget.setPointerCapture === "function") {
        event.currentTarget.setPointerCapture(event.pointerId);
      }
      setDragging(true);
    }

    event.preventDefault();
    event.currentTarget.scrollLeft = active.startScrollLeft - delta;
  };

  const finishDrag: PointerEventHandler<HTMLDivElement> = event => {
    const active = dragRef.current;
    if (!active || active.pointerId !== event.pointerId) return;

    dragRef.current = null;
    if (!active.moved) return;

    if (
      typeof event.currentTarget.hasPointerCapture === "function"
      && event.currentTarget.hasPointerCapture(event.pointerId)
      && typeof event.currentTarget.releasePointerCapture === "function"
    ) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    setDragging(false);
    window.setTimeout(() => {
      suppressClickRef.current = false;
    }, 0);
  };

  const onClickCapture: MouseEventHandler<HTMLDivElement> = event => {
    if (!suppressClickRef.current) return;
    event.preventDefault();
    event.stopPropagation();
  };

  return {
    dragging,
    dragHandlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: finishDrag,
      onPointerCancel: finishDrag,
      onClickCapture,
    },
  };
}
