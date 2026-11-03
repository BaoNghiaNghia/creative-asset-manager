import { describe, expect, it } from "vitest";
import { DEFAULT_FRONT_PLACEMENT } from "./FrontPlacement";
import {
  CROWN_FIT_CORNERS_8869, CROWN_FIT_POINT_LIMITS_8869, FRONT_CROWN_MASK_8869,
  insideCrown, insideCrownFitLimit, isValidCrownField, isValidQuad,
  moveCrownFieldPoint, projectCrownPoint, projectionFit,
} from "./ThreeQuarterProjection";

describe("8869 crown-aware three-quarter artwork projection", () => {
  it("calibrates all four limits to the actual front panel (not top of cap or visor)", () => {
    expect(CROWN_FIT_CORNERS_8869).toEqual([
      [0.336, 0.320], [0.638, 0.335], [0.652, 0.548], [0.296, 0.534],
    ]);
    expect(isValidCrownField(CROWN_FIT_CORNERS_8869)).toBe(true);
    expect(CROWN_FIT_CORNERS_8869.every((p, i) => insideCrownFitLimit(p, i))).toBe(true);
    for (const range of CROWN_FIT_POINT_LIMITS_8869) {
      for (const x of range.x) for (const y of range.y) {
        expect(insideCrown([x, y])).toBe(true);
      }
    }
    expect(isValidCrownField([
      [0.18, 0.12], [0.75, 0.24], [0.69, 0.69], [0.11, 0.56],
    ])).toBe(false); // screenshot's old oversize saved field
    expect(isValidCrownField([
      [0.336, 0.415], [0.638, 0.335], [0.652, 0.548], [0.296, 0.534],
    ])).toBe(false); // still crown interior but outside top-left safe window
    expect(isValidCrownField([
      [0.434, 0.400], [0.555, 0.401], [0.570, 0.458], [0.399, 0.459],
    ])).toBe(false); // collapse into unreadably small embroidery
  });

  it("constrains pointer and keyboard movement at crown limits with valid dimensions", () => {
    let next = moveCrownFieldPoint(CROWN_FIT_CORNERS_8869, 0, -10, -10);
    expect(next[0]).toEqual([0.310, 0.305]);
    expect(isValidCrownField(next)).toBe(true);
    next = moveCrownFieldPoint(next, 2, 20, 20);
    expect(next[2]).toEqual([0.676, 0.568]);
    expect(isValidCrownField(next)).toBe(true);
    const squeezed = moveCrownFieldPoint(CROWN_FIT_CORNERS_8869, 1, 0.555, 0.405);
    expect(squeezed[1][0]).toBeGreaterThan(0.555); // respects minimum upper width
    expect(isValidCrownField(squeezed)).toBe(true);
    expect(moveCrownFieldPoint(next, 1, Number.NaN, 0.36)).toEqual(next);
    expect(moveCrownFieldPoint(next, 9, 0.6, 0.3)).toEqual(next);
    expect(CROWN_FIT_CORNERS_8869[0]).toEqual([0.336, 0.320]); // immutable preset
  });

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
