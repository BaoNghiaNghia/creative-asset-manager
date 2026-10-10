import { describe, expect, it } from "vitest";
import { blueprintPixelBounds, blueprintFitArtwork } from "./BlueprintDesignFit";

function imagePixels(width: number, height: number, opaque = false) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  if (opaque) for (let i = 0; i < pixels.length; i += 4) {
    pixels[i] = 255; pixels[i + 1] = 255; pixels[i + 2] = 255; pixels[i + 3] = 255;
  }
  return pixels;
}
function fill(p: Uint8ClampedArray, width: number, x: number, y: number, rgb = 20) {
  const offset = (y * width + x) * 4;
  p[offset] = rgb; p[offset + 1] = rgb; p[offset + 2] = rgb; p[offset + 3] = 255;
}

describe("Blueprint front artwork auto-fit", () => {
  it("ignores transparent padding around the painted embroidery", () => {
    const p = imagePixels(100, 100);
    for (let y = 40; y < 60; y++) for (let x = 20; x < 80; x++) fill(p, 100, x, y);
    expect(blueprintPixelBounds(p, 100, 100)).toEqual({
      x: .19, y: .39, width: .62, height: .22,
    });
  });
  it("uses whitespace contrast for opaque white PNG previews", () => {
    const p = imagePixels(100, 100, true);
    for (let y = 30; y < 70; y++) for (let x = 30; x < 70; x++) fill(p, 100, x, y);
    expect(blueprintPixelBounds(p, 100, 100)).toEqual({
      x: .29, y: .29, width: .42, height: .42,
    });
  });
  it("centers the true embroidery silhouette and fills the dashed frame at 100%", () => {
    const g = blueprintFitArtwork(
      { width: 512, height: 512, bounds: { x: .3, y: .38, width: .4, height: .24 } },
      188, 140, 100, { maxWidth: 320, maxHeight: 190 },
    );
    const displayedW = Number(g.style.width) * .4;
    const displayedH = Number(g.style.height) * .24;
    expect(displayedW).toBeCloseTo(188 * .95, 2);
    expect(displayedH).toBeLessThanOrEqual(140 * .92);
    expect(Number(g.style.left) + Number(g.style.width) * .5).toBeCloseTo(94, 4);
    expect(Number(g.style.top) + Number(g.style.height) * .5).toBeCloseTo(70, 4);
    expect(g.effectivePercent).toBe(100);
  });
  it("limits embroidery height between the two horizontal guidelines even at 200%", () => {
    const d = { width: 512, height: 512, bounds: { x: .2, y: .2, width: .6, height: .6 } };
    const out = blueprintFitArtwork(d, 188, 84, 200, { maxWidth: 320, maxHeight: 132 });
    const visibleH = Number(out.style.height) * d.bounds.height;
    expect(visibleH).toBeLessThanOrEqual(132.01);
    expect(out.heightLimited).toBe(true);
    expect(out.effectivePercent).toBeLessThan(200);
  });
  it("falls back to a contain fit for empty or unreadable image data", () => {
    expect(blueprintPixelBounds(new Uint8ClampedArray(), 100, 100)).toEqual({ x: 0, y: 0, width: 1, height: 1 });
    expect(blueprintFitArtwork({ width: 900, height: 400, bounds: { x: 0, y: 0, width: 1, height: 1 } },
      188, 84, 100, { maxWidth: 320, maxHeight: 132 }).effectivePercent).toBe(100);
  });
});
