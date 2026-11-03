/**
 * Deterministic Stage 1 artwork projection onto the Valucap 8869 3/4 crown.
 *
 * Source artwork is stitched/alpha-only; do not run AI generation here.
 * Each saved quad is an OUTER embroidery field, not a bounding box to be
 * distorted blindly: trim transparent padding, preserve apparent glyph
 * proportions, respect FRONT master size and project with a curved mesh.
 * The user's editable four-point polygon is the placement boundary. The
 * optional stock mask is only applied when explicitly requested by the caller.
 */
import type { FrontPlacement } from "./FrontPlacement";
import { DEFAULT_FRONT_PLACEMENT } from "./FrontPlacement";

export type Point = [number, number];
export type ArtworkBounds = { x: number; y: number; width: number; height: number };
export type ProjectionSize = { width: number; height: number };
type CanvasSource = HTMLImageElement | HTMLCanvasElement;

const clamp = (n: number, min: number, max: number) => Math.min(max, Math.max(min, n));
const mix = (a: number, b: number, t: number) => a + (b - a) * t;

/**
 * Stock-calibrated contour of the front crown (not the visor or the side panel).
 * This is a conservative editable-preview mask for the synchronized 8869 3/4
 * factory view. Should stock crop/angle change, supply a new stock preset;
 * never substitute an unrelated 3/4 photograph.
 *
 * Coordinates refer to the square factory photo, normalized to [0,1].
 */
export const FRONT_CROWN_MASK_8869: readonly Point[] = Object.freeze([
  [0.251, 0.560], [0.245, 0.511], [0.252, 0.441], [0.268, 0.366],
  [0.298, 0.296], [0.333, 0.253], [0.378, 0.230], [0.443, 0.210],
  [0.520, 0.201], [0.584, 0.201], [0.638, 0.220], [0.662, 0.251],
  [0.675, 0.311], [0.684, 0.395], [0.694, 0.484], [0.699, 0.606],
  [0.649, 0.606], [0.576, 0.595], [0.496, 0.583], [0.411, 0.574],
  [0.330, 0.564],
]);

/** Front-panel embroidery field, not the whole hat silhouette.
 * Coordinates are normalized to the same square verified factory 3/4 photo.
 * Top corners sit below the curved crown top; bottom corners end ABOVE the
 * visor seam; the bottom is wider to follow the perspective of the panel.
 */
export const CROWN_FIT_CORNERS_8869: Point[] = [
  [0.336, 0.320], // 1: top left
  [0.638, 0.335], // 2: top right
  [0.652, 0.548], // 3: bottom right
  [0.296, 0.534], // 4: bottom left
];

export function isValidQuad(points: readonly Point[]): boolean {
  if (points.length !== 4 || points.some(p => p.length !== 2 || p.some(n => !Number.isFinite(n) || n < 0 || n > 1))) return false;
  const cross = (a: Point, b: Point, c: Point) =>
    (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  return points.every((_, index) => cross(
    points[index], points[(index + 1) % 4], points[(index + 2) % 4],
  ) >= 0.002);
}

export function insideCrown(point: Point, polygon: readonly Point[] = FRONT_CROWN_MASK_8869): boolean {
  let isInside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const [xi, yi] = polygon[i], [xj, yj] = polygon[j];
    if ((yi > point[1]) !== (yj > point[1]) &&
        point[0] < (xj - xi) * (point[1] - yi) / (yj - yi) + xi) isInside = !isInside;
  }
  return isInside;
}

/** Free drag: accept any point inside the image. The edited quad may be
 * temporarily crossed while moving a handle; only Save validates geometry.
 */
export function moveThreeQuarterPoint(
  points: readonly Point[], index: number, x: number, y: number,
): Point[] {
  if (!Number.isInteger(index) || index < 0 || index >= 4 ||
      !Number.isFinite(x) || !Number.isFinite(y)) return points.map(p => [...p] as Point);
  return points.map((p, i) => i === index
    ? [clamp(x, 0, 1), clamp(y, 0, 1)] as Point
    : [...p] as Point);
}

/** Scan only alpha, at preview resolution. A fully opaque reference uses its
 * full original box (no speculative white/black pixel deletion). */
export function visibleArtworkBounds(image: CanvasSource): ArtworkBounds {
  const width = image instanceof HTMLImageElement ? image.naturalWidth : image.width;
  const height = image instanceof HTMLImageElement ? image.naturalHeight : image.height;
  const fallback = { x: 0, y: 0, width: Math.max(1, width), height: Math.max(1, height) };
  if (!width || !height || typeof document === "undefined") return fallback;
  const side = 256;
  const canvas = document.createElement("canvas");
  canvas.width = side;
  canvas.height = side;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) return fallback;
  try {
    ctx.clearRect(0, 0, side, side);
    ctx.drawImage(image, 0, 0, side, side);
    const alpha = ctx.getImageData(0, 0, side, side).data;
    let x0 = side, y0 = side, x1 = -1, y1 = -1;
    for (let y = 0; y < side; y++) for (let x = 0; x < side; x++) {
      if (alpha[(y * side + x) * 4 + 3] <= 12) continue;
      x0 = Math.min(x0, x); x1 = Math.max(x1, x);
      y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    if (x1 < x0 || y1 < y0) return fallback;
    // Keep a little breathing room so anti-aliased thread edges survive.
    const pad = 2;
    x0 = Math.max(0, x0 - pad); y0 = Math.max(0, y0 - pad);
    x1 = Math.min(side - 1, x1 + pad); y1 = Math.min(side - 1, y1 + pad);
    return {
      x: x0 * width / side, y: y0 * height / side,
      width: (x1 + 1 - x0) * width / side,
      height: (y1 + 1 - y0) * height / side,
    };
  } catch {
    // A tainted image should not break the preview; the original remains safe.
    return fallback;
  }
}

function length(a: Point, b: Point) { return Math.hypot(a[0] - b[0], a[1] - b[1]); }

export type ProjectionFit = { uSpan: number; vSpan: number; inkAspect: number };
/** FRONT defines the relative size; 3/4 quad is the maximum projection field.
 * Keep original lettering proportions after accounting for angle foreshortening. */
export function projectionFit(
  corners: readonly Point[], bounds: ArtworkBounds,
  front: FrontPlacement, designSize: number,
): ProjectionFit {
  const quadW = (length(corners[0], corners[1]) + length(corners[3], corners[2])) / 2;
  const quadH = (length(corners[0], corners[3]) + length(corners[1], corners[2])) / 2;
  const referenceW = clamp(front.width / DEFAULT_FRONT_PLACEMENT.width * designSize / 100, 0.2, 1);
  const referenceH = clamp(front.height / DEFAULT_FRONT_PLACEMENT.height * designSize / 100, 0.2, 1);
  const availableW = quadW * 0.96 * referenceW;
  const availableH = quadH * 0.96 * referenceH;
  const sourceAspect = Math.max(0.1, bounds.width / Math.max(1, bounds.height));
  // A 35–45° view compresses horizontal lettering relative to the master FRONT.
  const apparentAspect = sourceAspect * 0.82;
  const fitW = Math.min(availableW, availableH * apparentAspect);
  const fitH = Math.min(availableH, availableW / apparentAspect);
  return { uSpan: clamp(fitW / Math.max(0.001, quadW), 0.01, 0.99),
    vSpan: clamp(fitH / Math.max(0.001, quadH), 0.01, 0.99),
    inkAspect: sourceAspect };
}

/** Returns normalized output coordinates. All four corners stay exact;
 * the interior is softly arched to suggest embroidery on a cylindrical crown. */
export function projectCrownPoint(
  corners: readonly Point[], u: number, v: number, curvature = 0.065,
): Point {
  const [tl, tr, br, bl] = corners;
  const barrel = 0.17;
  const su = mix(u, (1 - Math.cos(Math.PI * u)) / 2, barrel);
  const top: Point = [mix(tl[0], tr[0], su), mix(tl[1], tr[1], su)];
  const bottom: Point = [mix(bl[0], br[0], su), mix(bl[1], br[1], su)];
  const panelHeight = length(top, bottom);
  // Zero at the field perimeter, strongest halfway across/down the crown.
  const lift = Math.sin(Math.PI * u) * Math.sin(Math.PI * v) *
    clamp(curvature, 0, 0.09) * panelHeight;
  return [mix(top[0], bottom[0], v),
    mix(top[1], bottom[1], v) - lift];
}

function polygonClip(ctx: CanvasRenderingContext2D, polygon: readonly Point[], w: number, h: number) {
  ctx.beginPath();
  ctx.moveTo(polygon[0][0] * w, polygon[0][1] * h);
  for (const p of polygon.slice(1)) ctx.lineTo(p[0] * w, p[1] * h);
  ctx.closePath();
  ctx.clip();
}

/** Draw a source triangle with the exact affine map to its destination. */
function drawTriangle(
  ctx: CanvasRenderingContext2D, image: CanvasSource,
  source: readonly Point[], dest: readonly Point[],
  crop: ArtworkBounds,
) {
  const [s0, s1, s2] = source;
  const [d0, d1, d2] = dest;
  const x1 = s1[0] - s0[0], y1 = s1[1] - s0[1];
  const x2 = s2[0] - s0[0], y2 = s2[1] - s0[1];
  const det = x1 * y2 - x2 * y1;
  if (Math.abs(det) < 0.00001) return;
  const dx1 = d1[0] - d0[0], dy1 = d1[1] - d0[1];
  const dx2 = d2[0] - d0[0], dy2 = d2[1] - d0[1];
  const a = (dx1 * y2 - dx2 * y1) / det;
  const b = (dy1 * y2 - dy2 * y1) / det;
  const c = (dx2 * x1 - dx1 * x2) / det;
  const d = (dy2 * x1 - dy1 * x2) / det;
  const e = d0[0] - a * s0[0] - c * s0[1];
  const f = d0[1] - b * s0[0] - d * s0[1];
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(d0[0], d0[1]); ctx.lineTo(d1[0], d1[1]);
  ctx.lineTo(d2[0], d2[1]); ctx.closePath(); ctx.clip();
  ctx.setTransform(a, b, c, d, e, f);
  // Draw only this tiny mesh triangle's source rectangle (plus a 1px
  // seam margin). Drawing the entire image hundreds of times would stall
  // pointer dragging on lower-powered browsers.
  const lowX = Math.max(crop.x, Math.min(...source.map(p => p[0])) - 1);
  const lowY = Math.max(crop.y, Math.min(...source.map(p => p[1])) - 1);
  const highX = Math.min(crop.x + crop.width, Math.max(...source.map(p => p[0])) + 1);
  const highY = Math.min(crop.y + crop.height, Math.max(...source.map(p => p[1])) + 1);
  if (highX > lowX && highY > lowY) {
    ctx.drawImage(image, lowX, lowY, highX - lowX, highY - lowY,
      lowX, lowY, highX - lowX, highY - lowY);
  }
  ctx.restore();
}

export type CrownProjectionOptions = {
  corners: readonly Point[];
  front: FrontPlacement;
  designSize: number;
  curvature?: number;
  mask?: readonly Point[];
  width?: number;
  height?: number;
};
/** Deterministic, clipped, anti-aliased mesh. No network or AI calls per drag. */
export function renderCrownProjection(
  canvas: HTMLCanvasElement, image: CanvasSource, options: CrownProjectionOptions,
) {
  const w = options.width ?? 1000, h = options.height ?? 1000;
  if (canvas.width !== w) canvas.width = w;
  if (canvas.height !== h) canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  ctx.clearRect(0, 0, w, h);
  if (!isValidQuad(options.corners)) return;
  const bounds = visibleArtworkBounds(image);
  const fit = projectionFit(options.corners, bounds, options.front, options.designSize);
  const columns = 16, rows = 10;
  const point = (u: number, v: number): Point => {
    const uActual = 0.5 + (u - 0.5) * fit.uSpan;
    const vActual = 0.5 + (v - 0.5) * fit.vSpan;
    const [x, y] = projectCrownPoint(options.corners, uActual, vActual,
      options.curvature ?? 0.065);
    return [x * w, y * h];
  };
  const source = (u: number, v: number): Point => [
    bounds.x + u * bounds.width, bounds.y + v * bounds.height,
  ];
  ctx.save();
  polygonClip(ctx, options.corners, w, h);
  // Honor the user's manually fitted quadrilateral. A stock crown mask
  // would cut off valid, freely moved placements after the user adjusted them.
  if (options.mask) polygonClip(ctx, options.mask, w, h);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  for (let y = 0; y < rows; y++) for (let x = 0; x < columns; x++) {
    const u0 = x / columns, u1 = (x + 1) / columns;
    const v0 = y / rows, v1 = (y + 1) / rows;
    const s0 = source(u0, v0), s1 = source(u1, v0);
    const s2 = source(u1, v1), s3 = source(u0, v1);
    const d0 = point(u0, v0), d1 = point(u1, v0);
    const d2 = point(u1, v1), d3 = point(u0, v1);
    drawTriangle(ctx, image, [s0, s1, s2], [d0, d1, d2], bounds);
    drawTriangle(ctx, image, [s0, s2, s3], [d0, d2, d3], bounds);
  }
  ctx.restore();
}
