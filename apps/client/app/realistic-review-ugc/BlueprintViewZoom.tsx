import type { PointerEvent } from "react";

export const BLUEPRINT_VIEW_ZOOM_MIN = 75;
export const BLUEPRINT_VIEW_ZOOM_MAX = 200;
export const BLUEPRINT_VIEW_ZOOM_STEP = 25;

export function clampBlueprintViewZoom(value: number) {
  return Math.max(BLUEPRINT_VIEW_ZOOM_MIN, Math.min(BLUEPRINT_VIEW_ZOOM_MAX, value));
}

type Props = {
  label: "Front" | "Right side";
  value: number;
  onChange: (value: number) => void;
};

export function BlueprintViewZoom({ label, value, onChange }: Props) {
  // Zoom must never start the card's drag-to-change-design gesture.
  const stopDrag = (event: PointerEvent<HTMLDivElement>) => event.stopPropagation();
  return <div className="rrugc-blueprint-view-zoom" role="group" aria-label={label + " view zoom"}
    onPointerDown={stopDrag} onPointerMove={stopDrag} onPointerUp={stopDrag}
    onPointerCancel={stopDrag} onClick={event => event.stopPropagation()}>
    <button type="button" aria-label={"Zoom out " + label}
      disabled={value <= BLUEPRINT_VIEW_ZOOM_MIN}
      onClick={() => onChange(clampBlueprintViewZoom(value - BLUEPRINT_VIEW_ZOOM_STEP))}>−</button>
    <button type="button" className="rrugc-blueprint-view-zoom-value"
      aria-label={"Reset " + label + " zoom to 100%"} title="Reset zoom to 100%"
      onClick={() => onChange(100)}>{value}%</button>
    <button type="button" aria-label={"Zoom in " + label}
      disabled={value >= BLUEPRINT_VIEW_ZOOM_MAX}
      onClick={() => onChange(clampBlueprintViewZoom(value + BLUEPRINT_VIEW_ZOOM_STEP))}>+</button>
  </div>;
}
