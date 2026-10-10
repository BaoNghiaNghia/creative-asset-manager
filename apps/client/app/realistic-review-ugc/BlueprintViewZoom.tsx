import { useId, type PointerEvent } from "react";

/** Per-preview embroidery artwork scale. Never scales the underlying hat photo. */
export const BLUEPRINT_DESIGN_MIN = 40;
export const BLUEPRINT_DESIGN_MAX = 200;
export const BLUEPRINT_DESIGN_STEP = 5;

type Props = {
  label: "Front" | "Right side";
  value: number;
  onChange: (value: number) => void;
};

export function BlueprintViewZoom({ label, value, onChange }: Props) {
  const inputId = useId();
  // The card supports drag-to-browse; sliding should only resize the artwork.
  const stopDrag = (event: PointerEvent<HTMLDivElement>) => event.stopPropagation();
  return <div className="rrugc-blueprint-view-zoom" role="group" aria-label={label + " design size"}
    onPointerDown={stopDrag} onPointerMove={stopDrag} onPointerUp={stopDrag}
    onPointerCancel={stopDrag} onClick={event => event.stopPropagation()}>
    <label className="rrugc-blueprint-view-zoom-caption" htmlFor={inputId}>Design</label>
    <input id={inputId} className="rrugc-blueprint-view-zoom-slider" type="range"
      min={BLUEPRINT_DESIGN_MIN} max={BLUEPRINT_DESIGN_MAX} step={BLUEPRINT_DESIGN_STEP}
      value={value} aria-label={label + " design zoom"}
      onChange={event => onChange(Number(event.currentTarget.value))}/>
    <output className="rrugc-blueprint-view-zoom-value" htmlFor={inputId}>{value}%</output>
  </div>;
}
