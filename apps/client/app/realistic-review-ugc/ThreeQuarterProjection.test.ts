import { describe, expect, it, vi } from "vitest";
import { DEFAULT_FRONT_PLACEMENT } from "./FrontPlacement";
import {
  CROWN_FIT_CORNERS_8869, FRONT_CROWN_MASK_8869,
  insideCrown, isValidQuad, moveThreeQuarterPoint,
  projectCrownPoint, projectionFit, renderCrownProjection,
} from "./ThreeQuarterProjection";

describe("8869 crown-aware three-quarter artwork projection", () => {
  it("allows a manually fitted field outside the former crown windows", () => {
    const wide: [number, number][] = [[0.18, 0.12], [0.75, 0.24], [0.69, 0.69], [0.11, 0.56]];
    expect(isValidQuad(wide)).toBe(true);
    expect(wide.some(point => !insideCrown([...point]))).toBe(true);
    expect(isValidQuad([[0.10, 0.10], [0.90, 0.10], [0.90, 0.90], [0.10, 0.90]])).toBe(true);
    expect(isValidQuad([[0.434, 0.400], [0.555, 0.401], [0.570, 0.458], [0.399, 0.459]])).toBe(true);
  });

  it("moves each handle freely inside image bounds, including temporarily crossed fields", () => {
    const wide = moveThreeQuarterPoint(CROWN_FIT_CORNERS_8869, 0, 0.08, 0.1);
    expect(wide[0]).toEqual([0.08, 0.1]);
    expect(isValidQuad(wide)).toBe(true);
    const crossed = moveThreeQuarterPoint(wide, 1, 0.10, 0.98);
    expect(crossed[1]).toEqual([0.10, 0.98]);
    expect(isValidQuad(crossed)).toBe(false);
    expect(moveThreeQuarterPoint(crossed, 1, 0.9, 0.2)[1]).toEqual([0.9, 0.2]);
    expect(moveThreeQuarterPoint(wide, 3, -10, 20)[3]).toEqual([0, 1]);
    expect(moveThreeQuarterPoint(wide, 1, Number.NaN, 0.3)).toEqual(wide);
    expect(moveThreeQuarterPoint(wide, 9, 0.2, 0.3)).toEqual(wide);
  });

  it("clips projected embroidery to the freely dragged quad, not the fixed factory mask", () => {
    vi.stubGlobal("HTMLImageElement", class {});
    try {
      const clip = vi.fn();
      const ctx = {
        clearRect: vi.fn(), save: vi.fn(), restore: vi.fn(),
        beginPath: vi.fn(), moveTo: vi.fn(), lineTo: vi.fn(), closePath: vi.fn(),
        clip, setTransform: vi.fn(), drawImage: vi.fn(),
        imageSmoothingEnabled: false, imageSmoothingQuality: "low",
      };
      const canvas = { width: 1000, height: 1000, getContext: () => ctx } as unknown as HTMLCanvasElement;
      const source = { width: 120, height: 50 } as HTMLCanvasElement;
      const corners: [number, number][] = [
        [0.16, 0.10], [0.85, 0.15], [0.90, 0.77], [0.10, 0.73],
      ];
      renderCrownProjection(canvas, source, {
        corners, front: DEFAULT_FRONT_PLACEMENT, designSize: 100,
      });
      // 1 polygon clipping call for the user's field, plus 320 mesh triangle
      // clips. A second polygon clip here used to silently discard artwork.
      expect(clip).toHaveBeenCalledTimes(1 + 16 * 10 * 2);
      clip.mockClear();
      renderCrownProjection(canvas, source, {
        corners, front: DEFAULT_FRONT_PLACEMENT, designSize: 100, mask: FRONT_CROWN_MASK_8869,
      });
      expect(clip).toHaveBeenCalledTimes(2 + 16 * 10 * 2);
    } finally {
      vi.unstubAllGlobals();
    }
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
