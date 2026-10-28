import { describe, expect, it } from "vitest";
import { BLUEPRINT_HAT_COLORS } from "../BlueprintPreview";
import { analyzeThreadRegions, enhanceStitchDetail, materialCacheKey, prepareHatLighting, renderEmbroideryMaterial, type EmbroideryImage } from "./EmbroideryMaterialCompositor";

const create = (): EmbroideryImage => {
  const width = 40, height = 25, data = new Uint8ClampedArray(width * height * 4);
  for (let y = 7; y < 18; y++) for (let x = 7; x < 34; x++) {
    const i = (y * width + x) * 4;
    const groove = x % 3 === 0 ? -37 : x % 3 === 1 ? 0 : 37;
    const color = x < 21 ? [175 + groove, 53 + groove / 4, 35 + groove / 6]
      : [35 + groove / 6, 100 + groove / 2, 175 + groove];
    data.set([...color, 255], i);
  }
  return { data, width, height };
};
const at = (rgba: Uint8ClampedArray, width: number, x: number, y: number) =>
  Array.from(rgba.slice((y * width + x) * 4, (y * width + x) * 4 + 4));

describe("8869 embroidery color and material compositor", () => {
  it("splits warm and cool stitched regions and preserves high-frequency grooves", () => {
    const src = create();
    expect(analyzeThreadRegions(src).length).toBeGreaterThanOrEqual(2);
    const out = renderEmbroideryMaterial(src, "black", "auto");
    const redRegion = at(out, src.width, 10, 12);
    const blueRegion = at(out, src.width, 27, 12);
    expect(redRegion.slice(0, 3)).not.toEqual(blueRegion.slice(0, 3));
    expect(at(out, src.width, 11, 12).slice(0, 3)).not.toEqual(at(out, src.width, 12, 12).slice(0, 3));
    expect(at(src.data, src.width, 10, 12)[0]).toBeGreaterThan(100); // source never mutated
  });

  it("matches thread pigment to stock-backed cap colors without collapsing most palettes", () => {
    const src = create();
    const outputs = BLUEPRINT_HAT_COLORS.map(hat => {
      const out = renderEmbroideryMaterial(src, hat.id, "auto");
      return at(out, src.width, 11, 12).slice(0, 3).join(",");
    });
    expect(BLUEPRINT_HAT_COLORS).toHaveLength(12);
    // Distinct hat colors can share a close embroidery pigment after quantization;
    // require broad palette differentiation, not a one-to-one RGB fingerprint.
    expect(new Set(outputs).size).toBeGreaterThanOrEqual(Math.ceil(BLUEPRINT_HAT_COLORS.length * .8));
    expect(outputs[0]).not.toBe(outputs[1]);
  });

  it("keeps original pigment in Original mode but retains alpha and low-relief contact", () => {
    const src = create();
    const original = renderEmbroideryMaterial(src, "royal", "original");
    const auto = renderEmbroideryMaterial(src, "royal", "auto");
    expect(at(original, src.width, 20, 12).slice(0, 3)).not.toEqual(at(auto, src.width, 20, 12).slice(0, 3));
    // Contact is restricted to a tiny halo; margins stay fully transparent.
    expect(at(auto, src.width, 0, 0)).toEqual([0, 0, 0, 0]);
    expect(at(auto, src.width, 6, 12)[3]).toBeGreaterThan(0);
    expect(at(auto, src.width, 6, 12)[3]).toBeLessThan(40);
    expect(at(src.data, src.width, 5, 12)[3]).toBe(0);
  });

  it("samples ambient hat lighting without mutating the input hat data", () => {
    const pixels = new Uint8ClampedArray([
      90, 88, 80, 255, 230, 225, 210, 255,
      95, 94, 90, 255, 235, 233, 220, 255,
    ]);
    const saved = [...pixels];
    const lighting = prepareHatLighting({ data: pixels, width: 2, height: 2 });
    const src = create();
    const before = renderEmbroideryMaterial(src, "red", "auto");
    const after = renderEmbroideryMaterial(src, "red", "auto", 0, lighting);
    expect([...pixels]).toEqual(saved);
    expect(at(before, src.width, 11, 12)).not.toEqual(at(after, src.width, 11, 12));
  });

  it("sharpens existing thread microgrooves in the zoom detail without halos or changed transparency", () => {
    const width = 9, height = 9;
    const data = new Uint8ClampedArray(width * height * 4);
    for (let y = 1; y <= 7; y++) for (let x = 1; x <= 7; x++) {
      const i = (y * width + x) * 4;
      const shade = x === 4 ? 95 : 158;
      data.set([shade, shade, shade, 255], i);
    }
    const enhanced = enhanceStitchDetail(data, width, height);
    expect(enhanced[(4 * width + 4) * 4]).toBeLessThan(data[(4 * width + 4) * 4]);
    expect(enhanced[(4 * width + 3) * 4]).toBeGreaterThan(data[(4 * width + 3) * 4]);
    for (let i = 3; i < data.length; i += 4) {
      expect(enhanced[i]).toBe(data[i]); // original stitch silhouette unchanged
    }
    // No sharpening on transparent borders, edge stitches or fabric background.
    expect(at(enhanced, width, 0, 4)).toEqual(at(data, width, 0, 4));
    expect(at(enhanced, width, 1, 4)).toEqual(at(data, width, 1, 4));
    expect(at(data, width, 4, 4)).toEqual([95, 95, 95, 255]); // never mutate source
  });

  it("keeps source and cap color in the cache key, including mode and design identity", () => {
    const base = materialCacheKey("/art.png", "/black.jpg", "black", "auto");
    expect(base).not.toBe(materialCacheKey("/art.png", "/brown.jpg", "brown", "auto"));
    expect(base).not.toBe(materialCacheKey("/art.png", "/black.jpg", "black", "original"));
    expect(base).not.toBe(materialCacheKey("/art2.png", "/black.jpg", "black", "auto"));
    expect(base).not.toBe(materialCacheKey("/art.png", "/black.jpg", "black", "auto", 1));
    expect(base).not.toBe(materialCacheKey("/art.png", "/black.jpg", "black", "auto", 0, "side"));
  });

  it("never turns an opaque photographic canvas into a giant colored patch", () => {
    const width = 16, height = 16, data = new Uint8ClampedArray(width * height * 4);
    for (let i = 0; i < data.length; i += 4) data.set([88, 90, 93, 255], i);
    const out = renderEmbroideryMaterial({ width, height, data }, "red", "auto");
    expect([...out]).toEqual([...data]);
  });
});
