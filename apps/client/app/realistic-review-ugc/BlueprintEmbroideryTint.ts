import { useEffect, useState } from "react";
import { blueprintPaletteRgb, selectBlueprintThreadPalette, type BlueprintThreadPalette } from "./BlueprintThreadPalettes";

/** The thread colors match the dominant brim colors in the 12 supplied 8869 photos. */
export const BLUEPRINT_THREAD_COLORS: Record<string, string> = {
  black: "#141519",
  brown: "#5b4130",
  "camo-green": "#61594c",
  charcoal: "#394953",
  "forest-green": "#144935",
  khaki: "#ae9b8a",
  maroon: "#693043",
  "mossy-oak-breakup": "#4e523b",
  navy: "#202336",
  "realtree-all-purpose": "#655e50",
  red: "#c42039",
  royal: "#3b59a1",
};

const MAX_CACHE = 36;
const tintCache = new Map<string, string>();
const pending = new Map<string, Promise<string | null>>();
const keyFor = (src: string, colorId: string, designOrdinal: number) =>
  src + "##palette=" + colorId + ":" + designOrdinal;

function getPaletteColor(colorId: string) {
  return BLUEPRINT_THREAD_COLORS[colorId] ?? BLUEPRINT_THREAD_COLORS.black;
}
export function blueprintThreadRgb(colorId: string): [number, number, number] {
  const hex = getPaletteColor(colorId).slice(1);
  return [0, 2, 4].map(index => Number.parseInt(hex.slice(index, index + 2), 16)) as [number, number, number];
}

/** Tint only visible embroidery pixels; discard flat opaque preview backgrounds.
 * Contrast and alpha of the raised stitches survive recoloring.
 */
export function tintBlueprintPixels(
  data: Uint8ClampedArray, width: number, height: number,
  color: readonly [number, number, number],
): Uint8ClampedArray {
  const result = new Uint8ClampedArray(data);
  if (width <= 0 || height <= 0 || result.length < width * height * 4) return result;
  const corners = [
    0, (width - 1) * 4, (height - 1) * width * 4,
    ((height * width) - 1) * 4,
  ];
  const hasTransparency = corners.some(i => result[i + 3] < 245)
    || result.some((v, i) => i % 4 === 3 && v < 245);
  const bg = [0, 1, 2].map(c => Math.round(corners.reduce((sum, i) => sum + result[i + c], 0) / corners.length));

  const opacityAt = (i: number) => {
    if (hasTransparency) return result[i + 3] / 255;
    // The Skill sometimes supplies artwork rendered on an opaque light canvas.
    // Similar pixels to the canvas background must not turn into colored squares.
    const difference = Math.max(
      Math.abs(result[i] - bg[0]),
      Math.abs(result[i + 1] - bg[1]),
      Math.abs(result[i + 2] - bg[2]),
    );
    return Math.max(0, Math.min(1, (difference - 7) / 72));
  };
  let weight = 0, weightedLuma = 0;
  for (let i = 0; i < result.length; i += 4) {
    const opacity = opacityAt(i);
    if (opacity <= 0.015) continue;
    weight += opacity;
    weightedLuma += opacity * (result[i] * .2126 + result[i + 1] * .7152 + result[i + 2] * .0722);
  }
  const mean = weight ? weightedLuma / weight : 85;
  for (let i = 0; i < result.length; i += 4) {
    const opacity = opacityAt(i);
    if (opacity <= 0.015) {
      result[i] = result[i + 1] = result[i + 2] = result[i + 3] = 0;
      continue;
    }
    const luma = result[i] * .2126 + result[i + 1] * .7152 + result[i + 2] * .0722;
    // Keep dark stitch grooves and brighter raised threads without reintroducing
    // any of the original black/red/blue embroidery hue.
    const lighting = Math.max(.58, Math.min(1.45, 1 + (luma - mean) / 175));
    for (let channel = 0; channel < 3; channel++) {
      result[i + channel] = Math.max(0, Math.min(255, Math.round(color[channel] * lighting)));
    }
    result[i + 3] = Math.round(opacity * 255);
  }
  return result;
}


/** Re-map colored artwork into four dark embroidery threads, retaining its
 * original stitch shading and outline. Unlike flat tint, warm/cool lettering
 * and pale stitching use different threads within the chosen palette.
 */
/** Slightly darken stitched edges without drawing a thick black outline.
 * A large outline contrast reads as a raised patch when placed over twill.
 */
export function thinEmbroideryEdgeColor(
  primary: readonly number[], outline: readonly number[],
): [number, number, number] {
  return [0, 1, 2].map(i =>
    Math.round(primary[i] * .82 + outline[i] * .18)) as [number, number, number];
}

export function remapBlueprintPixels(
  data: Uint8ClampedArray, width: number, height: number,
  palette: BlueprintThreadPalette,
): Uint8ClampedArray {
  const result = new Uint8ClampedArray(data);
  if (width <= 0 || height <= 0 || result.length < width * height * 4) return result;
  const corners = [0, (width - 1) * 4, (height - 1) * width * 4, (width * height - 1) * 4];
  let hasAlpha = false;
  for (let i = 3; i < data.length; i += 4) {
    if (data[i] < 245) { hasAlpha = true; break; }
  }
  const background = [0, 1, 2].map(c =>
    Math.round(corners.reduce((sum, i) => sum + data[i + c], 0) / corners.length));
  const count = width * height;
  const opacity = new Float32Array(count);
  const histogram = new Float32Array(18);
  let sumLight = 0, total = 0, saturated = 0;
  for (let pixel = 0; pixel < count; pixel++) {
    const i = pixel * 4;
    const visibility = hasAlpha ? data[i + 3] / 255 : Math.min(1, Math.max(0,
      (Math.max(
        Math.abs(data[i] - background[0]),
        Math.abs(data[i + 1] - background[1]),
        Math.abs(data[i + 2] - background[2]),
      ) - 7) / 72));
    opacity[pixel] = visibility;
    if (visibility <= .015) continue;
    const luma = data[i] * .2126 + data[i + 1] * .7152 + data[i + 2] * .0722;
    sumLight += luma * visibility;
    total += visibility;
    const chroma = Math.max(data[i], data[i + 1], data[i + 2]) -
      Math.min(data[i], data[i + 1], data[i + 2]);
    const saturation = chroma / Math.max(1, Math.max(data[i], data[i + 1], data[i + 2]));
    if (chroma > 35 && saturation > .23) {
      const bin = hueBucket(data[i], data[i + 1], data[i + 2]);
      histogram[bin] += visibility;
      saturated += visibility;
    }
  }
  const mean = total > 0 ? sumLight / total : 85;
  const primaryBin = histogram.indexOf(Math.max(...histogram));
  const circularDistance = (a: number, b: number) => Math.min(Math.abs(a - b), 18 - Math.abs(a - b));
  let secondaryBin = -1, secondWeight = 0;
  for (let bin = 0; bin < histogram.length; bin++) {
    if (circularDistance(bin, primaryBin) >= 3 && histogram[bin] > secondWeight) {
      secondaryBin = bin; secondWeight = histogram[bin];
    }
  }
  const hasSeparateHue = secondWeight > saturated * .13 && secondWeight > 2;
  const hasChromaticArtwork = saturated > total * .12;
  const threads = {
    primary: blueprintPaletteRgb(palette.primary),
    secondary: blueprintPaletteRgb(palette.secondary),
    accent: blueprintPaletteRgb(palette.accent),
    outline: blueprintPaletteRgb(palette.outline),
  };
  const thinOutline = thinEmbroideryEdgeColor(threads.primary, threads.outline);
  for (let pixel = 0; pixel < count; pixel++) {
    const i = pixel * 4;
    const alpha = opacity[pixel];
    if (alpha <= .015) {
      result[i] = result[i + 1] = result[i + 2] = result[i + 3] = 0;
      continue;
    }
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const light = r * .2126 + g * .7152 + b * .0722;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    const chroma = max - min;
    const strongColor = chroma > 35 && chroma / Math.max(1, max) > .23;
    const secondaryHue = strongColor && hasSeparateHue
      && circularDistance(hueBucket(r, g, b), secondaryBin)
        < circularDistance(hueBucket(r, g, b), primaryBin);
    let role: keyof typeof threads = "primary";
    if (secondaryHue) {
      role = "secondary";
    } else if (!strongColor && light > Math.max(160, mean + 43) && hasChromaticArtwork) {
      role = "accent";
    } else if ((!strongColor && light > mean + 30) || (strongColor && light > mean + 55)) {
      role = "secondary";
    }
    if (light < mean - 36 && role === "primary") role = "outline";
    // A low-relief satin/fill stitch has restrained edge shading, not
    // a black beveled ring around each glyph.
    const rgb = role === "outline" ? thinOutline : threads[role];
    const brightness = Math.max(.84, Math.min(1.16, 1 + (light - mean) / 270));
    result[i] = Math.min(255, Math.round(rgb[0] * brightness));
    result[i + 1] = Math.min(255, Math.round(rgb[1] * brightness));
    result[i + 2] = Math.min(255, Math.round(rgb[2] * brightness));
    result[i + 3] = Math.round(alpha * 255);
  }
  return result;
}

function hueBucket(red: number, green: number, blue: number): number {
  const r = red / 255, g = green / 255, b = blue / 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b);
  const diff = max - min;
  if (diff < .001) return 0;
  let hue = max === r ? ((g - b) / diff) % 6
    : max === g ? (b - r) / diff + 2
    : (r - g) / diff + 4;
  hue = (hue * 60 + 360) % 360;
  return Math.min(17, Math.floor(hue / 20));
}

async function tintSource(src: string, colorId: string, designOrdinal: number): Promise<string | null> {
  if (typeof document === "undefined" || typeof Image === "undefined") return null;
  const image = new Image();
  image.crossOrigin = "anonymous";
  image.decoding = "async";
  image.src = src;
  try {
    await image.decode();
    const width = image.naturalWidth, height = image.naturalHeight;
    if (!width || !height || width * height > 4_000_000) return null;
    const canvas = document.createElement("canvas");
    canvas.width = width; canvas.height = height;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) return null;
    ctx.drawImage(image, 0, 0);
    const pixels = ctx.getImageData(0, 0, width, height);
    pixels.data.set(remapBlueprintPixels(pixels.data, width, height,
      selectBlueprintThreadPalette(colorId, designOrdinal)));
    ctx.putImageData(pixels, 0, 0);
    return canvas.toDataURL("image/png");
  } catch {
    // Failed CORS or decode: always preserve the original design rather than
    // incorrectly tinting the whole hat or blocking navigation.
    return null;
  } finally {
    image.src = "";
  }
}

function remember(key: string, result: string) {
  tintCache.delete(key);
  tintCache.set(key, result);
  while (tintCache.size > MAX_CACHE) tintCache.delete(tintCache.keys().next().value!);
}

export function useBlueprintEmbroideryTint(src: string, colorId: string, designOrdinal = 0) {
  const key = keyFor(src, colorId, designOrdinal);
  const [loaded, setLoaded] = useState<{ key: string; src: string } | null>(null);
  useEffect(() => {
    if (!src) return;
    let cancelled = false;
    const cached = tintCache.get(key);
    if (cached) {
      setLoaded({ key, src: cached });
      return;
    }
    let request = pending.get(key);
    if (!request) {
      request = tintSource(src, colorId, designOrdinal);
      pending.set(key, request);
    }
    request.then(result => {
      if (result) remember(key, result);
      if (!cancelled) setLoaded({ key, src: result || src });
    }).finally(() => {
      if (pending.get(key) === request) pending.delete(key);
    });
    return () => { cancelled = true; };
  }, [src, colorId, designOrdinal, key]);
  return loaded?.key === key ? loaded.src : tintCache.get(key) || src;
}