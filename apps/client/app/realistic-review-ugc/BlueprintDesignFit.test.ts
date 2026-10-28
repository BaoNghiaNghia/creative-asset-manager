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
  it("fits the dashed preview outline tightly around a wide logo without stretching typography", () => {
    const frameWidth = 295, frameHeight = 132;
    const bounds = { x: .1, y: .42, width: .8, height: .16 };
    const result = blueprintFitArtwork({ width: 512, height: 512, bounds },
      frameWidth, frameHeight, 100, { maxWidth: frameWidth * .98, maxHeight: frameHeight * .98 });
    const actualLeft = Number(result.style.left) + bounds.x * Number(result.style.width);
    const actualTop = Number(result.style.top) + bounds.y * Number(result.style.height);
    const actualWidth = Number(result.style.width) * bounds.width;
    const actualHeight = Number(result.style.height) * bounds.height;
    const left = Number(result.guideStyle.left), top = Number(result.guideStyle.top);
    const width = Number(result.guideStyle.width), height = Number(result.guideStyle.height);
    expect(left).toBeGreaterThanOrEqual(0);
    expect(top).toBeGreaterThanOrEqual(0);
    expect(left + width).toBeLessThanOrEqual(frameWidth);
    expect(top + height).toBeLessThanOrEqual(frameHeight);
    expect(left).toBeLessThan(actualLeft);
    expect(top).toBeLessThan(actualTop);
    expect(left + width).toBeGreaterThan(actualLeft + actualWidth);
    expect(top + height).toBeGreaterThan(actualTop + actualHeight);
    expect(height).toBeLessThan(frameHeight * .75);
    expect(width).toBeGreaterThan(frameWidth * .95);
    expect(Number(result.style.width) / Number(result.style.height)).toBeCloseTo(1, 8);
  });
  it("keeps the fitted dashed area inside the original placement bounds at all zoom levels", () => {
    for (const bounds of [
      { x: .15, y: .42, width: .7, height: .16 },
      { x: .42, y: .1, width: .16, height: .8 },
      { x: .26, y: .26, width: .48, height: .48 },
    ]) for (const zoom of [40, 100, 150, 200]) {
      const result = blueprintFitArtwork({ width: 512, height: 512, bounds },
        295, 132, zoom, { maxWidth: 295 * .98, maxHeight: 132 * .98 });
      const left = Number(result.guideStyle.left), top = Number(result.guideStyle.top);
      const width = Number(result.guideStyle.width), height = Number(result.guideStyle.height);
      expect(left).toBeGreaterThanOrEqual(0);
      expect(top).toBeGreaterThanOrEqual(0);
      expect(left + width).toBeLessThanOrEqual(295.00001);
      expect(top + height).toBeLessThanOrEqual(132.00001);
      expect(width).toBeGreaterThan(0);
      expect(height).toBeGreaterThan(0);
    }
  });
  it("limits embroidery height between the two horizontal guidelines even at 200%", () => {
    const d = { width: 512, height: 512, bounds: { x: .2, y: .2, width: .6, height: .6 } };
    const out = blueprintFitArtwork(d, 188, 84, 200, { maxWidth: 320, maxHeight: 132 });
    const visibleH = Number(out.style.height) * d.bounds.height;
    expect(visibleH).toBeLessThanOrEqual(132.01);
    expect(out.heightLimited).toBe(true);
    expect(out.effectivePercent).toBeLessThan(200);
  });
  it("keeps wide and tall painted artwork inside the expanded embroidery field even at 200% zoom", () => {
    // A 700×500 front scene: 69.6% × 23.1% is the cyan placement field.
    const width = 700 * .696;
    const height = 500 * .231;
    const candidates = [
      { width: 512, height: 512, bounds: { x: .08, y: .43, width: .84, height: .14 } },
      { width: 512, height: 512, bounds: { x: .36, y: .12, width: .28, height: .76 } },
    ];
    for (const d of candidates) for (const zoom of [100, 150, 200]) {
      const fitted = blueprintFitArtwork(
        d, width, height, zoom,
        // Legacy upper/lower blue lines allow more height than the cyan box.
        // The fitted artwork must still stay within its actual dashed frame.
        { maxWidth: width * .98, maxHeight: height * (.37 / .21) },
      );
      const visibleLeft = Number(fitted.style.left) + Number(fitted.style.width) * d.bounds.x;
      const visibleRight = visibleLeft + Number(fitted.style.width) * d.bounds.width;
      const visibleTop = Number(fitted.style.top) + Number(fitted.style.height) * d.bounds.y;
      const visibleBottom = visibleTop + Number(fitted.style.height) * d.bounds.height;
      expect(visibleLeft).toBeGreaterThanOrEqual(0);
      expect(visibleTop).toBeGreaterThanOrEqual(0);
      expect(visibleRight).toBeLessThanOrEqual(width);
      expect(visibleBottom).toBeLessThanOrEqual(height);
      // No nonuniform scaling or cropping of the actual embroidery.
      expect(Number(fitted.style.width) / Number(fitted.style.height)).toBeCloseTo(1, 8);
    }
  });

  it("falls back to a contain fit for empty or unreadable image data", () => {
    expect(blueprintPixelBounds(new Uint8ClampedArray(), 100, 100)).toEqual({ x: 0, y: 0, width: 1, height: 1 });
    expect(blueprintFitArtwork({ width: 900, height: 400, bounds: { x: 0, y: 0, width: 1, height: 1 } },
      188, 84, 100, { maxWidth: 320, maxHeight: 132 }).effectivePercent).toBe(100);
  });
});