import { useRef, type KeyboardEvent, type PointerEvent } from "react";
import { dragFrontPlacement, type FrontPlacement, type PlacementDragMode } from "./FrontPlacement";
import "./FrontPlacementEditor.css";

type Drag = {
  pointer: number;
  mode: PlacementDragMode;
  start: FrontPlacement;
  x: number;
  y: number;
  width: number;
  height: number;
};

const HANDLES = [
  ["nw", "Top left"], ["n", "Top"], ["ne", "Top right"],
  ["e", "Right"], ["se", "Bottom right"], ["s", "Bottom"],
  ["sw", "Bottom left"], ["w", "Left"],
] as const;

export function FrontPlacementEditor({ value, onChange }: {
  value: FrontPlacement;
  onChange: (value: FrontPlacement) => void;
}) {
  const activeDrag = useRef<Drag | null>(null);

  function start(event: PointerEvent<HTMLElement>, mode: PlacementDragMode) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    const scene = event.currentTarget.closest<HTMLElement>(".rrugc-blueprint-front-scene");
    const bounds = scene?.getBoundingClientRect();
    if (!bounds?.width || !bounds?.height) return;
    event.preventDefault();
    event.stopPropagation();
    activeDrag.current = {
      pointer: event.pointerId, mode, start: value,
      x: event.clientX, y: event.clientY, width: bounds.width, height: bounds.height,
    };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  }

  function move(event: PointerEvent<HTMLElement>) {
    const drag = activeDrag.current;
    if (!drag || drag.pointer !== event.pointerId) return;
    event.preventDefault();
    event.stopPropagation();
    onChange(dragFrontPlacement(drag.start, drag.mode,
      (event.clientX - drag.x) / drag.width,
      (event.clientY - drag.y) / drag.height));
  }

  function finish(event: PointerEvent<HTMLElement>) {
    if (activeDrag.current?.pointer !== event.pointerId) return;
    event.stopPropagation();
    activeDrag.current = null;
    if (event.currentTarget.hasPointerCapture?.(event.pointerId)) {
      event.currentTarget.releasePointerCapture?.(event.pointerId);
    }
  }

  function keydown(event: KeyboardEvent<HTMLElement>, mode: PlacementDragMode) {
    const dx = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const dy = event.key === "ArrowDown" ? 1 : event.key === "ArrowUp" ? -1 : 0;
    if (!dx && !dy) return;
    event.preventDefault();
    event.stopPropagation();
    const step = event.shiftKey ? 0.02 : 0.005;
    onChange(dragFrontPlacement(value, mode, dx * step, dy * step));
  }

  const gestures = (mode: PlacementDragMode) => ({
    onPointerDown: (event: PointerEvent<HTMLElement>) => start(event, mode),
    onPointerMove: move,
    onPointerUp: finish,
    onPointerCancel: finish,
    onKeyDown: (event: KeyboardEvent<HTMLElement>) => keydown(event, mode),
    onClick: (event: React.MouseEvent<HTMLElement>) => {
      event.preventDefault();
      event.stopPropagation();
    },
  });

  return <div className="rrugc-front-placement-editor" aria-label="Edit front embroidery placement">
    <div className="rrugc-front-placement-field" role="button" tabIndex={0}
      aria-label="Move front embroidery area. Drag or use arrow keys."
      title="Drag to move · Arrow keys for precise placement"
      style={{
        left: `${value.left * 100}%`, top: `${value.top * 100}%`,
        width: `${value.width * 100}%`, height: `${value.height * 100}%`,
      }} {...gestures("move")}>
      <span className="rrugc-front-placement-caption" aria-hidden="true">Front embroidery area</span>
      {HANDLES.map(([mode, label]) =>
        <span key={mode} role="button" tabIndex={0}
          aria-label={`Resize embroidery area: ${label}`}
          title={`Resize: ${label}`}
          className={`rrugc-front-placement-handle is-${mode}`}
          {...gestures(mode)} />)}
    </div>
    {(["upper", "lower"] as const).map(mode =>
      <div key={mode} role="slider" tabIndex={0}
        aria-label={mode === "upper" ? "Move upper blue height guide" : "Move lower blue height guide"}
        aria-orientation="vertical"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(value[mode === "upper" ? "upper_line" : "lower_line"] * 100)}
        aria-valuetext={`${Math.round(value[mode === "upper" ? "upper_line" : "lower_line"] * 100)} percent of photo height`}
        title="Drag vertically · Arrow keys for 0.5% steps"
        className={`rrugc-front-placement-height is-${mode}`}
        style={{ top: `${value[mode === "upper" ? "upper_line" : "lower_line"] * 100}%` }}
        {...gestures(mode)}>
        <span aria-hidden="true" />
      </div>)}
  </div>;
}
