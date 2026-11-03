import { describe, expect, it } from "vitest";
import { DEFAULT_FRONT_PLACEMENT } from "./FrontPlacement";
import {
  CROWN_FIT_CORNERS_8869, FRONT_CROWN_MASK_8869,
  insideCrown, isValidQuad, projectCrownPoint, projectionFit,
} from "./ThreeQuarterProjection";

describe("8869 crown-aware three-quarter artwork projection", () => {
  it("provides safe factory points within the front panel instead of the brim or side seam", () => {
    expect(CROWN_FIT_CORNERS_8869).toHaveLength(4);
    expect(FRONT_CROWN_MASK_8869.length).toBeGreaterThan(15);
    expect(isValidQuad(CROWN_FIT_CORNERS_8869)).toBe(true);
    expect(CROWN_FIT_CORNERS_8869.every(point => insideCrown(point))).toBe(true);
    expect(insideCrown([0.185, 0.12])).toBe(false); // old freehand guide above the cap
    expect(insideCrown([0.82, 0.57])).toBe(false); // GatorHats side panel
    expect(insideCrown([0.4, 0.73])).toBe(false); // visor
  });

  it("preserves the exact four reference corners; the interior arcs along the crown", () => {
    const quad = CROWN_FIT_CORNERS_8869;
    for (const [index, uv] of [[0, [0, 0]], [1, [1, 0]], [2, [1, 1]], [3, [0, 1]]] as const) {
      const output = projectCrownPoint(quad, uv[0], uv[1], 0.07);
      expect(output[0]).toBeCloseTo(quad[index][0], 7);
      expect(output[1]).toBeCloseTo(quad[index][1], 7);
    }
    const flatCenter = projectCrownPoint(quad, 0.5, 0.5, 0);
    const curvedCenter = projectCrownPoint(quad, 0.5, 0.5, 0.065);
    expect(curvedCenter[1]).toBeLessThan(flatCenter[1] - 0.008);
    expect(curvedCenter[0]).toBeCloseTo(flatCenter[0], 6);
    expect(isValidQuad([[0.2, 0.2], [0.7, 0.4], [0.3, 0.6], [0.7, 0.3]])).toBe(false);
  });

  it("fits trimmed lettering within the field and follows FRONT sizing without stretching", () => {
    const bounds = { x: 250, y: 190, width: 1000, height: 510 };
    const full = projectionFit(CROWN_FIT_CORNERS_8869, bounds, DEFAULT_FRONT_PLACEMENT, 100);
    expect(full.uSpan).toBeGreaterThan(0.80);
    expect(full.vSpan).toBeGreaterThan(0.80);
    expect(full.uSpan).toBeLessThan(1);
    expect(full.vSpan).toBeLessThan(1);
    const small = projectionFit(CROWN_FIT_CORNERS_8869, bounds,
      { ...DEFAULT_FRONT_PLACEMENT, width: DEFAULT_FRONT_PLACEMENT.width / 2 }, 75);
    expect(small.uSpan).toBeLessThan(full.uSpan);
    expect(small.vSpan).toBeLessThan(full.vSpan);
    expect(full.inkAspect).toBeCloseTo(bounds.width / bounds.height);
  });
});
