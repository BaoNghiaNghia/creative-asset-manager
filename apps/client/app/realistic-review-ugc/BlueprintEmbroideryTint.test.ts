import { describe, expect, it } from "vitest";
import { BLUEPRINT_THREAD_COLORS, blueprintThreadRgb, tintBlueprintPixels } from "./BlueprintEmbroideryTint";

const setPixel = (a: Uint8ClampedArray, x: number, rgb: readonly number[], alpha = 255) => {
  const i = x * 4;
  a.set([rgb[0], rgb[1], rgb[2], alpha], i);
};

describe("Blueprint embroidery matches 12 cap brims", () => {
  it("defines a stable distinct thread color for each of the 12 colorways", () => {
    expect(Object.keys(BLUEPRINT_THREAD_COLORS)).toHaveLength(12);
    expect(new Set(Object.values(BLUEPRINT_THREAD_COLORS)).size).toBe(12);
    expect(blueprintThreadRgb("maroon")).toEqual([105, 48, 67]);
    expect(blueprintThreadRgb("royal")).toEqual([59, 89, 161]);
    expect(blueprintThreadRgb("black")).toEqual([20, 21, 25]);
  });

  it("tints only the embroidery, preserves transparent backgrounds and stitch relief", () => {
    const source = new Uint8ClampedArray(4 * 3);
    setPixel(source, 0, [255, 255, 255], 0);
    setPixel(source, 1, [24, 30, 27]);
    setPixel(source, 2, [80, 84, 80], 190);
    const blue = tintBlueprintPixels(source, 3, 1, blueprintThreadRgb("royal"));
    expect(blue.slice(0, 4)).toEqual(new Uint8ClampedArray([0, 0, 0, 0]));
    expect(blue[7]).toBe(255);
    expect(blue[11]).toBe(190);
    expect(blue[6]).toBeGreaterThan(blue[4]); // more blue than red
    expect(blue[8]).toBeGreaterThan(blue[4]); // highlighted raised stitches
    expect(source[4]).toBe(24); // source is never mutated
    const burgundy = tintBlueprintPixels(source, 3, 1, blueprintThreadRgb("maroon"));
    expect(burgundy[4]).toBeGreaterThan(burgundy[6]); // burgundy: R > B
    expect(blue[4]).not.toBe(burgundy[4]);
  });

  it("removes opaque white/near-white PNG margins instead of tinting a rectangle", () => {
    const pixels = new Uint8ClampedArray(4 * 7);
    for (let x = 0; x < 7; x++) setPixel(pixels, x, [255, 255, 255]);
    setPixel(pixels, 1, [251, 252, 252]); // almost-white antialiasing
    setPixel(pixels, 2, [180, 180, 180]); // antialiased edge
    setPixel(pixels, 3, [27, 27, 27]); // dark stitch
    setPixel(pixels, 4, [75, 75, 75]); // raised stitch highlight
    const out = tintBlueprintPixels(pixels, 7, 1, blueprintThreadRgb("brown"));
    expect(out[3]).toBe(0);
    expect(out[7]).toBe(0);
    expect(out[2 * 4 + 3]).toBeGreaterThan(0);
    expect(out[3 * 4 + 3]).toBe(255);
    expect(out[4 * 4]).toBeGreaterThan(out[3 * 4]);
    expect(out[6 * 4 + 3]).toBe(0);
  });

  it("retains shape for multi-color lettering while using one thread hue", () => {
    const source = new Uint8ClampedArray([
      0, 0, 0, 0, 255, 32, 16, 255, 16, 50, 255, 255, 55, 55, 55, 128,
    ]);
    const green = tintBlueprintPixels(source, 4, 1, blueprintThreadRgb("forest-green"));
    for (let i = 4; i < green.length; i += 4) {
      expect(green[i + 1]).toBeGreaterThan(green[i]);
      expect(green[i + 1]).toBeGreaterThan(green[i + 2]);
    }
    expect(green[15]).toBe(128);
  });
});