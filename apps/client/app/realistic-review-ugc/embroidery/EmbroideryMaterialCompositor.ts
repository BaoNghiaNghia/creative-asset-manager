import { blueprintPaletteRgb, selectBlueprintThreadPalette } from "../BlueprintThreadPalettes";
import { remapBlueprintPixels } from "../BlueprintEmbroideryTint";

/**
 * 8869 material preview, not an embroidery machine file.
 * Operates at the design's source resolution, independent of CSS/card size.
 * The transparent canvas stays the SAME dimensions as the source artwork so
 * BlueprintDesignFit and the zoom dialog share an identical placement.
 */
export type ThreadMode = "auto" | "original";
export const EMBROIDERY_MATERIAL_VERSION = "8869-material-v1";
export type EmbroideryImage = { data: Uint8ClampedArray; width: number; height: number };
type Region = { hue: number; weight: number; mean: number; count: number };
const limit = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const luma = (r: number, g: number, b: number) => r * .2126 + g * .7152 + b * .0722;

function hueInfo(r: number, g: number, b: number): [number, number] {
  const top = Math.max(r, g, b), bottom = Math.min(r, g, b);
  const delta = top - bottom;
  if (!delta || top === 0) return [0, 0];
  let h = top === r ? ((g - b) / delta) % 6
    : top === g ? (b - r) / delta + 2 : (r - g) / delta + 4;
  if (h < 0) h += 6;
  return [h / 6, delta / top];
}
const hueDistance = (a: number, b: number) => {
  const delta = Math.abs(a - b);
  return Math.min(delta, 1 - delta);
};

/** Main thread colors are chosen from chromatic stitched areas, not shadows. */
export function analyzeThreadRegions(src: EmbroideryImage): Region[] {
  const bins = Array.from({ length: 24 }, (_, i) => ({ hue: (i + .5) / 24, weight: 0, mean: 0, count: 0 }));
  let total = 0;
  for (let i = 0; i < src.data.length; i += 4) {
    const a = src.data[i + 3] / 255;
    if (a < .28) continue;
    const [h, s] = hueInfo(src.data[i], src.data[i + 1], src.data[i + 2]);
    if (s < .21) continue;
    const lum = luma(src.data[i], src.data[i + 1], src.data[i + 2]);
    if (lum < 26) continue;
    const w = a * Math.sqrt(s);
    const bin = bins[Math.floor(h * 24) % 24];
    bin.weight += w;
    bin.mean += lum * w;
    bin.count++;
    total += w;
  }
  const regions: Region[] = [];
  const scored = bins.map((b, i) => {
    const prev = bins[(i + 23) % 24].weight, next = bins[(i + 1) % 24].weight;
    return { ...b, weight: b.weight + (prev + next) * .48 };
  }).sort((a, b) => b.weight - a.weight);
  // A second thread must be a meaningfully separate hue, not the highlights
  // or dark outlines surrounding the first one.
  for (const candidate of scored) {
    if (candidate.weight < Math.max(1.5, total * .095)) break;
    if (regions.some(r => hueDistance(r.hue, candidate.hue) < .12)) continue;
    regions.push({ ...candidate });
    if (regions.length === 3) break;
  }
  if (!regions.length) regions.push({ hue: 0, weight: 1, mean: 105, count: 1 });
  // Estimate source luminance for each chromatic region; this lets us keep
  // micro stitch contrast when a warm/cool design is recolored.
  for (const region of regions) {
    let sum = 0, weight = 0;
    for (let i = 0; i < src.data.length; i += 4) {
      const a = src.data[i + 3] / 255;
      if (a < .35) continue;
      const [h, s] = hueInfo(src.data[i], src.data[i + 1], src.data[i + 2]);
      if (s < .12 || hueDistance(h, region.hue) > .10) continue;
      const y = luma(src.data[i], src.data[i + 1], src.data[i + 2]);
      if (y < 26) continue;
      sum += y * a; weight += a;
    }
    region.mean = weight ? sum / weight : 105;
  }
  return regions;
}

function findBounds(data: Uint8ClampedArray, width: number, height: number) {
  let left = width, top = height, right = -1, bottom = -1, visible = 0;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    if (data[i + 3] < 20) continue;
    visible++;
    left = Math.min(left, x); top = Math.min(top, y);
    right = Math.max(right, x); bottom = Math.max(bottom, y);
  }
  return { left, top, width: Math.max(1, right - left + 1), height: Math.max(1, bottom - top + 1), visible };
}

/** Bilinear, alpha-premultiplied sampling prevents pale halos around stitches. */
function sample(data: Uint8ClampedArray, width: number, height: number, x: number, y: number): [number, number, number, number] {
  const x0 = Math.floor(x), y0 = Math.floor(y);
  let alpha = 0, red = 0, green = 0, blue = 0;
  for (let dy = 0; dy < 2; dy++) for (let dx = 0; dx < 2; dx++) {
    const sx = x0 + dx, sy = y0 + dy;
    if (sx < 0 || sy < 0 || sx >= width || sy >= height) continue;
    const weight = (dx ? x - x0 : 1 - (x - x0)) * (dy ? y - y0 : 1 - (y - y0));
    const p = (sy * width + sx) * 4, a = data[p + 3] / 255 * weight;
    alpha += a; red += data[p] * a; green += data[p + 1] * a; blue += data[p + 2] * a;
  }
  if (alpha < .002) return [0, 0, 0, 0];
  return [red / alpha, green / alpha, blue / alpha, alpha * 255];
}

export type HatLighting = { pixels: Uint8ClampedArray; width: number; height: number; average: number };
export function prepareHatLighting(hat: EmbroideryImage): HatLighting {
  // Each pixel is a low-frequency crown-lighting tile, NOT a fabric print.
  const { width, height, data } = hat;
  let sum = 0;
  const pixels = new Uint8ClampedArray(data);
  for (let i = 0; i < pixels.length; i += 4) sum += luma(pixels[i], pixels[i + 1], pixels[i + 2]);
  return { pixels, width, height, average: sum / Math.max(1, width * height) };
}

function hatIllumination(hat: HatLighting | undefined, nx: number, ny: number) {
  if (!hat) return 1;
  const x = limit(Math.round(nx * (hat.width - 1)), 0, hat.width - 1);
  const y = limit(Math.round(ny * (hat.height - 1)), 0, hat.height - 1);
  const p = (y * hat.width + x) * 4;
  const brightness = luma(hat.pixels[p], hat.pixels[p + 1], hat.pixels[p + 2]);
  return limit(1 + (brightness - hat.average) / 380, .92, 1.08);
}

/**
 * Low-relief thread simulation: surface-conforming arc, palette-per-region,
 * original high-frequency stitch lighting, and a sub-thread contact shadow.
 * Neither the hat image nor any pixel outside embroidery/shadow is touched.
 */
export function renderEmbroideryMaterial(
  source: EmbroideryImage,
  colorId: string,
  mode: ThreadMode,
  designOrdinal = 0,
  lighting?: HatLighting,
  surface: "front" | "side" = "front",
): Uint8ClampedArray {
  const { width, height } = source;
  const prepared = remapBlueprintPixels(source.data, width, height);
  const bounds = findBounds(prepared, width, height);
  // Never infer a matte from a photo: opaque backgrounds must NOT be
  // interpreted as embroidery and must not receive a rectangular shadow.
  if (!bounds.visible || bounds.visible > width * height * .975) return prepared;
  const input = { data: prepared, width, height };
  const regions = analyzeThreadRegions(input);
  const palette = selectBlueprintThreadPalette(colorId, designOrdinal);
  const colors = [palette.primary, palette.secondary, palette.accent].map(blueprintPaletteRgb);
  const warped = new Uint8ClampedArray(prepared.length);
  const centerX = bounds.left + bounds.width / 2;
  const half = Math.max(1, bounds.width / 2);
  const arc = surface === "front" ? Math.min(height * .024, bounds.height * .075) : Math.min(height * .008, bounds.height * .03);
  for (let y = Math.max(0, Math.floor(bounds.top - 6)); y < Math.min(height, Math.ceil(bounds.top + bounds.height + arc + 7)); y++) {
    for (let x = Math.max(0, Math.floor(bounds.left - 5)); x < Math.min(width, Math.ceil(bounds.left + bounds.width + 5)); x++) {
      const u = limit((x - centerX) / half, -1.08, 1.08);
      const squeeze = 1 - (surface === "front" ? .024 : .008) * u * u;
      const sx = centerX + (x - centerX) / squeeze;
      const sy = y - arc * u * u;
      const [r, g, b, a] = sample(prepared, width, height, sx, sy);
      if (a < 2) continue;
      let rgb = [r, g, b];
      if (mode === "auto") {
        const [h, sat] = hueInfo(r, g, b);
        let nearest = 0, distance = Infinity;
        for (let j = 0; j < regions.length; j++) {
          const d = hueDistance(h, regions[j].hue);
          if (d < distance) { distance = d; nearest = j; }
        }
        // Low-saturation grooves belong to the dominant stitch region.
        if (sat < .12) nearest = 0;
        const base = colors[nearest];
        const ySrc = luma(r, g, b);
        const mean = regions[nearest].mean;
        const lightingFactor = limit(1 + (ySrc - mean) / 200, .52, 1.42);
        rgb = base.map(v => v * lightingFactor);
      }
      const nx = limit((x - bounds.left) / bounds.width, 0, 1);
      const ny = limit((y - bounds.top) / bounds.height, 0, 1);
      const hatGain = hatIllumination(lighting, nx, ny);
      // A subtle lighting ridge only; this is NOT faux bevel/patch extrusion.
      const pos = (y * width + x) * 4;
      for (let c = 0; c < 3; c++) warped[pos + c] = limit(Math.round(rgb[c] * hatGain), 0, 255);
      warped[pos + 3] = Math.round(a);
    }
  }
  const output = new Uint8ClampedArray(warped);
  // Dark contact, physically limited to the outside of stitch alpha. Source
  // resolution rather than display CSS px keeps zoom and thumbnail identical.
  const radius = Math.max(1, Math.min(3, Math.round(width / 900)));
  for (let y = Math.max(1, bounds.top - 3); y < Math.min(height - 1, bounds.top + bounds.height + arc + 4); y++) {
    for (let x = Math.max(1, bounds.left - 3); x < Math.min(width - 1, bounds.left + bounds.width + 4); x++) {
      const p = (y * width + x) * 4;
      if (warped[p + 3] >= 245) continue;
      let contact = 0;
      for (let dy = -radius; dy <= radius; dy++) for (let dx = -radius; dx <= radius; dx++) {
        const xx = x + dx, yy = y + dy;
        if (xx < 0 || xx >= width || yy < 0 || yy >= height) continue;
        const shadowWeight = (1 - Math.abs(dx) / (radius + 1)) * (1 - Math.abs(dy) / (radius + 1));
        // Directional under-thread contact, not a broad floating shadow.
        const q = (yy * width + xx) * 4 + 3;
        contact = Math.max(contact, warped[q] / 255 * shadowWeight);
      }
      if (contact === 0) continue;
      const shadowAlpha = Math.round(contact * 27 * (1 - warped[p + 3] / 255));
      if (shadowAlpha < 1) continue;
      // Existing colored edge stays intact; only transparent fringe receives shadow.
      const oldA = output[p + 3] / 255, newA = shadowAlpha / 255;
      const mixed = oldA + newA * (1 - oldA);
      const shadow = [37, 31, 25];
      for (let c = 0; c < 3; c++) output[p + c] = Math.round((output[p + c] * oldA + shadow[c] * newA * (1 - oldA)) / mixed);
      output[p + 3] = Math.round(mixed * 255);
    }
  }
  return output;
}

/**
 * Restore a little of the original one-pixel thread-groove contrast for zoom
 * inspection. Operates only inside fully opaque stitches whose 4 neighbors
 * are also opaque, keeping edges, thread outline, alpha and hat unchanged.
 * This is not synthetic upscaling: a low-resolution master stays limited by
 * its own captured stitch detail.
 */
export function enhanceStitchDetail(
  rgba: Uint8ClampedArray, width: number, height: number,
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(rgba);
  if (width < 3 || height < 3 || rgba.length < width * height * 4) return output;
  const pitch = width * 4;
  for (let y = 1; y < height - 1; y++) {
    for (let x = 1; x < width - 1; x++) {
      const p = (y * width + x) * 4;
      if (rgba[p + 3] < 245 ||
          rgba[p - 4 + 3] < 245 || rgba[p + 4 + 3] < 245 ||
          rgba[p - pitch + 3] < 245 || rgba[p + pitch + 3] < 245) continue;
      for (let channel = 0; channel < 3; channel++) {
        const average = (
          rgba[p - 4 + channel] + rgba[p + 4 + channel] +
          rgba[p - pitch + channel] + rgba[p + pitch + channel]
        ) * .25;
        const localDetail = limit(rgba[p + channel] - average, -32, 32);
        output[p + channel] = Math.round(limit(rgba[p + channel] + localDetail * .38, 0, 255));
      }
    }
  }
  return output;
}

export function materialCacheKey(src: string, hatSrc: string, colorId: string, mode: ThreadMode, ordinal = 0, surface = "front") {
  return [EMBROIDERY_MATERIAL_VERSION, src, hatSrc, colorId, mode, ordinal, surface].join("##");
}
