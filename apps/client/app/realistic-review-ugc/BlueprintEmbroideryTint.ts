import { useEffect, useState } from "react";

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
const keyFor = (src: string, colorId: string) => src + "##thread=" + colorId;

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

async function tintSource(src: string, colorId: string): Promise<string | null> {
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
    pixels.data.set(tintBlueprintPixels(pixels.data, width, height, blueprintThreadRgb(colorId)));
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

export function useBlueprintEmbroideryTint(src: string, colorId: string) {
  const key = keyFor(src, colorId);
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
      request = tintSource(src, colorId);
      pending.set(key, request);
    }
    request.then(result => {
      if (result) remember(key, result);
      if (!cancelled) setLoaded({ key, src: result || src });
    }).finally(() => {
      if (pending.get(key) === request) pending.delete(key);
    });
    return () => { cancelled = true; };
  }, [src, colorId, key]);
  return loaded?.key === key ? loaded.src : tintCache.get(key) || src;
}