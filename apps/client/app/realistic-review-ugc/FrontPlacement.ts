import type { CSSProperties } from "react";

/** Fractions of the original 4:5 stock photo, independent of display zoom. */
export type FrontPlacement = {
  left: number;
  top: number;
  width: number;
  height: number;
  upper_line: number;
  lower_line: number;
  revision: number;
  updated_at?: string | null;
};

export const DEFAULT_FRONT_PLACEMENT: FrontPlacement = Object.freeze({
  left: 0.152, top: 0.309, width: 0.696, height: 0.231,
  upper_line: 0.19, lower_line: 0.63, revision: 0, updated_at: null,
});

type PlacementGeometry = Pick<FrontPlacement,
  "left" | "top" | "width" | "height" | "upper_line" | "lower_line">;

export type PlacementDragMode = "move" | "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw" | "upper" | "lower";

export function frontPlacementStyle(placement: PlacementGeometry): CSSProperties {
  return {
    "--rrugc-guide-left": `${placement.left * 100}%`,
    "--rrugc-guide-top": `${placement.top * 100}%`,
    "--rrugc-guide-width": `${placement.width * 100}%`,
    "--rrugc-guide-height": `${placement.height * 100}%`,
    "--rrugc-upper-line": `${placement.upper_line * 100}%`,
    "--rrugc-lower-line": `${placement.lower_line * 100}%`,
  } as CSSProperties;
}

export function frontPlacementChanged(first: PlacementGeometry, other: PlacementGeometry): boolean {
  return (["left", "top", "width", "height", "upper_line", "lower_line"] as const)
    .some(key => Math.abs(first[key] - other[key]) > 0.000001);
}

const bound = (v: number, low: number, high: number) => Math.max(low, Math.min(high, v));
const round = (v: number) => Math.round(v * 1000000) / 1000000;

/** Pure, bounded geometry for mouse/touch and keyboard; 8 handles resize the field. */
export function dragFrontPlacement(
  start: FrontPlacement, mode: PlacementDragMode, dx: number, dy: number,
): FrontPlacement {
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return start;
  const next = { ...start };
  if (mode === "upper") {
    next.upper_line = round(bound(start.upper_line + dy, 0, start.lower_line - 0.04));
  } else if (mode === "lower") {
    next.lower_line = round(bound(start.lower_line + dy, start.upper_line + 0.04, 1));
  } else if (mode === "move") {
    next.left = round(bound(start.left + dx, 0, 1 - start.width));
    next.top = round(bound(start.top + dy, 0, 1 - start.height));
  } else {
    const right = start.left + start.width;
    const bottom = start.top + start.height;
    if (mode.includes("w")) {
      next.left = round(bound(start.left + dx, 0, right - 0.1));
      next.width = round(right - next.left);
    }
    if (mode.includes("e")) next.width = round(bound(start.width + dx, 0.1, 1 - start.left));
    if (mode.includes("n")) {
      next.top = round(bound(start.top + dy, 0, bottom - 0.06));
      next.height = round(bottom - next.top);
    }
    if (mode.includes("s")) next.height = round(bound(start.height + dy, 0.06, 1 - start.top));
  }
  return next;
}

export function placementForSave(p: FrontPlacement) {
  return {
    left: p.left, top: p.top, width: p.width, height: p.height,
    upper_line: p.upper_line, lower_line: p.lower_line,
    expected_revision: p.revision,
  };
}
