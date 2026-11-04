// Compatibility only: historical 4-corner records remain readable by the API,
// but the production Slot 2 projector uses the single 5x5 JSON grid instead.
export type { Point as Corner } from "./ThreeQuarterProjection";
import type { Point } from "./ThreeQuarterProjection";
export const DEFAULT_THREE_QUARTER_CORNERS: readonly Point[] = [
  [.336, .320], [.638, .335], [.652, .548], [.296, .534],
];
