import type { PointerEvent as ReactPointerEvent } from "react";

export const BLUEPRINT_LENS_SIZE = 164;
export const BLUEPRINT_LENS_ZOOM = 2.4;

const clamp = (value: number, min: number, max: number) => Math.max(min, Math.min(max, value));

/** Keep the circular lens inside its image while magnifying the exact pointer sample. */
export function blueprintLensGeometry(
  x: number, y: number, width: number, height: number,
  size = BLUEPRINT_LENS_SIZE, zoom = BLUEPRINT_LENS_ZOOM,
) {
  const diameter = Math.min(size, width, height);
  const radius = diameter / 2;
  return {
    diameter,
    left: clamp(x - radius, 0, width - diameter),
    top: clamp(y - radius, 0, height - diameter),
    translateX: radius - zoom * x,
    translateY: radius - zoom * y,
  };
}

export function hideBlueprintLens(lens: HTMLDivElement | null) {
  if (lens) lens.style.opacity = "0";
}

/** Imperative updates avoid a React re-render for every mouse movement. */
export function moveBlueprintLens(event: ReactPointerEvent<HTMLDivElement>, lens: HTMLDivElement | null, ready = true) {
  if (!lens || !ready || (event.pointerType !== "mouse" && event.pointerType !== "pen") || event.buttons !== 0) {
    hideBlueprintLens(lens);
    return;
  }
  const parent = event.currentTarget;
  const rect = parent.getBoundingClientRect();
  const width = parent.clientWidth;
  const height = parent.clientHeight;
  if (!rect.width || !rect.height || !width || !height) return;
  const x = clamp((event.clientX - rect.left) * width / rect.width, 0, width);
  const y = clamp((event.clientY - rect.top) * height / rect.height, 0, height);
  const geometry = blueprintLensGeometry(x, y, width, height);
  const scene = lens.firstElementChild as HTMLElement | null;
  if (!scene) return;
  lens.style.left = geometry.left + "px";
  lens.style.top = geometry.top + "px";
  lens.style.width = geometry.diameter + "px";
  lens.style.height = geometry.diameter + "px";
  scene.style.width = width + "px";
  scene.style.height = height + "px";
  scene.style.transform = `translate3d(${geometry.translateX}px,${geometry.translateY}px,0) scale(${BLUEPRINT_LENS_ZOOM})`;
  lens.style.opacity = "1";
}
