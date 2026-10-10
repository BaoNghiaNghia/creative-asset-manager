import { useEffect, useRef, useState, type CSSProperties, type SyntheticEvent } from "react";

/** Normalized nontransparent artwork content box (ignore empty PNG margins). */
export type DesignBounds = { x: number; y: number; width: number; height: number };
type ImageInfo = { source: string; width: number; height: number; bounds: DesignBounds };
const cachedArtwork = new Map<string, ImageInfo>();
const FULL_IMAGE: DesignBounds = { x: 0, y: 0, width: 1, height: 1 };

export function blueprintPixelBounds(
  rgba: Uint8ClampedArray, width: number, height: number,
): DesignBounds {
  if (width <= 0 || height <= 0 || rgba.length < width * height * 4) return FULL_IMAGE;
  const first = [rgba[0], rgba[1], rgba[2]];
  let hasTransparency = false;
  for (let offset = 3; offset < rgba.length; offset += 4) {
    if (rgba[offset] < 245) { hasTransparency = true; break; }
  }
  let left = width, top = height, right = -1, bottom = -1;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const i = (y * width + x) * 4;
    const visible = hasTransparency
      ? rgba[i + 3] >= 24
      : Math.max(Math.abs(rgba[i] - first[0]), Math.abs(rgba[i + 1] - first[1]), Math.abs(rgba[i + 2] - first[2])) >= 27;
    if (!visible) continue;
    left = Math.min(left, x); top = Math.min(top, y);
    right = Math.max(right, x); bottom = Math.max(bottom, y);
  }
  if (right < left || bottom < top) return FULL_IMAGE;
  // Keep a small antialiasing margin around fine stitches.
  left = Math.max(0, left - 1); top = Math.max(0, top - 1);
  right = Math.min(width, right + 2); bottom = Math.min(height, bottom + 2);
  return { x: left / width, y: top / height, width: (right - left) / width, height: (bottom - top) / height };
}

export function inspectBlueprintArtwork(image: HTMLImageElement, source: string): ImageInfo {
  const width = image.naturalWidth || image.width || 1;
  const height = image.naturalHeight || image.height || 1;
  const existing = cachedArtwork.get(source);
  if (existing) return existing;
  let bounds = FULL_IMAGE;
  try {
    const canvas = document.createElement("canvas");
    const ratio = Math.min(1, 256 / Math.max(width, height));
    canvas.width = Math.max(1, Math.round(width * ratio));
    canvas.height = Math.max(1, Math.round(height * ratio));
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (ctx) {
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height);
      bounds = blueprintPixelBounds(pixels.data, canvas.width, canvas.height);
    }
  } catch {
    // Cross-origin/tainted canvases: preserve an ordinary contain-fit.
  }
  const info = { source, width, height, bounds };
  cachedArtwork.set(source, info);
  return info;
}

export function blueprintFitArtwork(
  info: Pick<ImageInfo, "width" | "height" | "bounds">,
  frameWidth: number, frameHeight: number, percent: number,
  limits: { maxWidth: number; maxHeight: number },
): { style: CSSProperties; effectivePercent: number; heightLimited: boolean } {
  const b = info.bounds;
  const contentWidth = Math.max(1, b.width * info.width);
  const contentHeight = Math.max(1, b.height * info.height);
  const base = Math.min(frameWidth * .95 / contentWidth, frameHeight * .92 / contentHeight);
  const zoom = Math.max(.4, Math.min(2, percent / 100));
  const heightCap = limits.maxHeight / (contentHeight * base);
  const widthCap = limits.maxWidth / (contentWidth * base);
  const actual = Math.min(zoom, heightCap, widthCap);
  const renderedWidth = Math.max(1, info.width * base * actual);
  const renderedHeight = Math.max(1, info.height * base * actual);
  return {
    style: {
      position: "absolute", width: renderedWidth, height: renderedHeight,
      maxWidth: "none", maxHeight: "none", objectFit: "fill",
      left: frameWidth / 2 - (b.x + b.width / 2) * renderedWidth,
      top: frameHeight / 2 - (b.y + b.height / 2) * renderedHeight,
      transform: "none",
    },
    effectivePercent: Math.round(actual * 100),
    heightLimited: zoom > heightCap + .0001,
  };
}

export function useBlueprintArtworkFit(source: string, percent: number) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState({ width: 0, height: 0 });
  const [image, setImage] = useState<ImageInfo | null>(() => cachedArtwork.get(source) ?? null);
  useEffect(() => {
    const element = frameRef.current;
    if (!element) return;
    const update = () => setFrame(old => {
      const width = element.clientWidth, height = element.clientHeight;
      return old.width === width && old.height === height ? old : { width, height };
    });
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  const onLoad = (event: SyntheticEvent<HTMLImageElement>) => {
    setImage(inspectBlueprintArtwork(event.currentTarget, source));
  };
  const ready = image?.source === source && frame.width > 0 && frame.height > 0;
  const fitted = ready
    ? blueprintFitArtwork(
      image, frame.width, frame.height, percent,
      // Keep visible stitches inside the wider cyan field and between the
      // 19%-63% height lines even when Design size is increased to 200%.
      { maxWidth: frame.width * .98, maxHeight: frame.height * (.37 / .21) },
    ) : null;
  return { frameRef, onLoad, style: fitted?.style, ready, fitted };
}
