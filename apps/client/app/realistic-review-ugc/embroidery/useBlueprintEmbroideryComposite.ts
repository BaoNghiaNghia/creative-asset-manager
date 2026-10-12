import { useEffect, useState } from "react";
import {
  materialCacheKey, prepareHatLighting, renderEmbroideryMaterial,
  type ThreadMode, type EmbroideryImage, type HatLighting,
} from "./EmbroideryMaterialCompositor";

const MAX_DIMENSION = 1800;
const MAX_ENTRIES = 16;
const images = new Map<string, Promise<HTMLImageElement>>();
const results = new Map<string, string>();
const inFlight = new Map<string, Promise<string>>();
const makeCanvas = (width: number, height: number) => {
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  return canvas;
};
function decodedImage(src: string): Promise<HTMLImageElement> {
  const existing = images.get(src);
  if (existing) return existing;
  const task = new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.crossOrigin = "anonymous";
    image.decoding = "async";
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error("Embroidery image unavailable"));
    image.src = src;
    if (typeof image.decode === "function") image.decode().then(() => resolve(image)).catch(() => { /* onerror handles failures */ });
  });
  images.set(src, task);
  // Do not pin failed image requests in cache; retries should work.
  task.catch(() => { if (images.get(src) === task) images.delete(src); });
  while (images.size > 18) images.delete(images.keys().next().value!);
  return task;
}
function readPixels(image: HTMLImageElement): EmbroideryImage {
  const scale = Math.min(1, MAX_DIMENSION / Math.max(image.naturalWidth, image.naturalHeight));
  const width = Math.max(1, Math.round(image.naturalWidth * scale));
  const height = Math.max(1, Math.round(image.naturalHeight * scale));
  const canvas = makeCanvas(width, height);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("Canvas 2D not supported");
  ctx.drawImage(image, 0, 0, width, height);
  return { data: ctx.getImageData(0, 0, width, height).data, width, height };
}
function readCrownLighting(hat: HTMLImageElement): HatLighting | undefined {
  const canvas = makeCanvas(32, 24);
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return undefined;
  // Crop only the 8869 crown; never average the black brim or white margins.
  ctx.drawImage(hat, hat.naturalWidth * .21, hat.naturalHeight * .305,
    hat.naturalWidth * .58, hat.naturalHeight * .245, 0, 0, 32, 24);
  const data = ctx.getImageData(0, 0, 32, 24);
  return prepareHatLighting({ data: data.data, width: 32, height: 24 });
}
async function composite(src: string, hatSrc: string, colorId: string, mode: ThreadMode, ordinal: number, surface: "front" | "side") {
  const design = await decodedImage(src);
  // A missing/tainted hat image must not block artwork preview. It merely
  // disables ambient lighting; other material effects still apply.
  const cap = hatSrc ? await decodedImage(hatSrc).catch(() => null) : null;
  const artwork = readPixels(design);
  let lighting: HatLighting | undefined;
  if (cap) {
    try { lighting = readCrownLighting(cap); } catch { /* CORS/tainted hat: neutral lighting */ }
  }
  const rgba = renderEmbroideryMaterial(artwork, colorId, mode, ordinal, lighting, surface);
  const output = makeCanvas(artwork.width, artwork.height);
  const ctx = output.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D not supported");
  const img = ctx.createImageData(artwork.width, artwork.height);
  img.data.set(rgba);
  ctx.putImageData(img, 0, 0);
  const blob = await new Promise<Blob>((resolve, reject) => {
    output.toBlob(file => file ? resolve(file) : reject(new Error("PNG export failed")), "image/png");
  });
  return URL.createObjectURL(blob);
}
function remember(key: string, src: string) {
  results.delete(key); results.set(key, src);
  while (results.size > MAX_ENTRIES) {
    const oldest = results.keys().next().value!;
    const url = results.get(oldest)!;
    results.delete(oldest);
    URL.revokeObjectURL(url);
  }
}
/**
 * A single design + color + cap-surface material pipeline is shared by the
 * fixed Blueprint, hover lens and detail modal. Original keeps master pigment
 * while still conforming to the cap and creating thin contact shading.
 */
export function useBlueprintEmbroideryComposite(
  src: string, hatSrc: string, colorId: string,
  mode: ThreadMode = "auto", ordinal = 0, surface: "front" | "side" = "front",
) {
  const key = materialCacheKey(src, hatSrc, colorId, mode, ordinal, surface);
  const [loaded, setLoaded] = useState<{ key: string; src: string } | null>(null);
  useEffect(() => {
    if (!src || typeof document === "undefined") return;
    let cancelled = false;
    const cached = results.get(key);
    if (cached) {
      setLoaded({ key, src: cached });
      return;
    }
    let task = inFlight.get(key);
    if (!task) {
      task = composite(src, hatSrc, colorId, mode, ordinal, surface);
      inFlight.set(key, task);
    }
    task.then(result => {
      remember(key, result);
      if (!cancelled) setLoaded({ key, src: result });
    }).catch(() => {
      if (!cancelled) setLoaded({ key, src }); // preserve approved artwork if CORS, image or canvas fails
    }).finally(() => {
      if (inFlight.get(key) === task) inFlight.delete(key);
    });
    return () => { cancelled = true; };
  }, [key, src, hatSrc, colorId, mode, ordinal, surface]);
  return loaded?.key === key ? loaded.src : results.get(key) || src;
}
