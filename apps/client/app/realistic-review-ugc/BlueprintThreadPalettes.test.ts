import { describe, expect, it } from "vitest";
import { BLUEPRINT_DARK_PALETTES, blueprintPaletteRgb, selectBlueprintThreadPalette } from "./BlueprintThreadPalettes";
import { remapBlueprintPixels } from "./BlueprintEmbroideryTint";

function sample(pixels: Array<{ rgb: [number, number, number]; alpha?: number }>) {
  const bytes = new Uint8ClampedArray(pixels.length * 4);
  pixels.forEach(({ rgb, alpha }, i) => bytes.set([...rgb, alpha ?? 255], i * 4));
  return bytes;
}
function rgb(bytes: Uint8ClampedArray, i: number) {
  return Array.from(bytes.slice(i * 4, i * 4 + 3));
}
describe("Blueprint dark multi-thread palettes", () => {
  it("provides 3–5 dark combinations for every supplied colorway", () => {
    expect(Object.keys(BLUEPRINT_DARK_PALETTES)).toHaveLength(12);
    for (const options of Object.values(BLUEPRINT_DARK_PALETTES)) {
      expect(options.length).toBeGreaterThanOrEqual(3);
      expect(options.length).toBeLessThanOrEqual(5);
      expect(new Set(options.map(item => item.name)).size).toBe(options.length);
      for (const item of options) {
        for (const thread of [item.primary, item.secondary, item.accent, item.outline]) {
          expect(thread).toMatch(/^#[0-9a-f]{6}$/i);
        }
        expect(item.primary).not.toBe(item.secondary);
        expect(item.primary).not.toBe(item.outline);
      }
    }
  });
  it("rotates deterministically between distinct looks for adjacent designs and colorways", () => {
    const looks = Array.from({ length: 5 }, (_, index) =>
      selectBlueprintThreadPalette("black", index).name);
    expect(new Set(looks).size).toBe(5);
    expect(selectBlueprintThreadPalette("black", 0)).toBe(selectBlueprintThreadPalette("black", 5));
    expect(selectBlueprintThreadPalette("brown", 0).name).toBe("Dark Cocoa");
    expect(selectBlueprintThreadPalette("brown", 1).name).toBe("Oxblood");
    expect(selectBlueprintThreadPalette("missing", 2).name).toBe("Espresso");
    expect(blueprintPaletteRgb("#123456")).toEqual([18, 52, 86]);
  });
  it("retains separate colored regions rather than flattening coral and blue into one black tone", () => {
    const row = [
      { rgb: [0, 0, 0] as [number, number, number], alpha: 0 },
      ...Array.from({ length: 4 }, () => ({ rgb: [225, 55, 39] as [number, number, number] })),
      ...Array.from({ length: 4 }, () => ({ rgb: [37, 71, 213] as [number, number, number] })),
      { rgb: [226, 220, 194] as [number, number, number] },
      { rgb: [40, 40, 40] as [number, number, number], alpha: 176 },
    ];
    const original = sample(row);
    const mapped = remapBlueprintPixels(original, row.length, 1, selectBlueprintThreadPalette("black", 2));
    expect(mapped[3]).toBe(0);
    expect(mapped[4 + 3]).toBe(255);
    expect(mapped[10 * 4 + 3]).toBe(176);
    expect(rgb(mapped, 1)).not.toEqual(rgb(mapped, 5));
    expect(rgb(mapped, 1)).not.toEqual([0, 0, 0]);
    expect(rgb(mapped, 5)).not.toEqual([0, 0, 0]);
    expect(rgb(original, 1)).toEqual([225, 55, 39]); // source unchanged
  });
  it("keeps stitch shadows and raised highlights in single-color design artwork", () => {
    const input = sample([
      { rgb: [0, 0, 0], alpha: 0 },
      { rgb: [20, 22, 24] },
      { rgb: [47, 49, 52] },
      { rgb: [118, 120, 124] },
      { rgb: [90, 90, 90], alpha: 115 },
    ]);
    const out = remapBlueprintPixels(input, 5, 1, selectBlueprintThreadPalette("black", 0));
    expect(rgb(out, 1)).not.toEqual(rgb(out, 2));
    expect(rgb(out, 2)).not.toEqual(rgb(out, 3));
    expect(out[3]).toBe(0);
    expect(out[19]).toBe(115);
  });
  it("strips opaque white margins instead of creating a colored rectangle", () => {
    const input = sample([
      { rgb: [255,255,255] },
      { rgb: [252,252,251] },
      { rgb: [169,169,169] },
      { rgb: [28,28,28] },
      { rgb: [255,255,255] },
    ]);
    const out = remapBlueprintPixels(input, 5, 1, selectBlueprintThreadPalette("navy", 1));
    expect(out[3]).toBe(0);
    expect(out[7]).toBe(0);
    expect(out[11]).toBeGreaterThan(0);
    expect(out[15]).toBeGreaterThan(0);
    expect(out[19]).toBe(0);
  });
});
