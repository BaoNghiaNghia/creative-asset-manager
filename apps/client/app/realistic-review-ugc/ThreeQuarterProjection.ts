/** One canonical alpha-trimmed design texture, projected through the pinned
 * 5x5 profile mesh into FRONT and 3/4. Nothing is fitted to the visible half.
 * Geometry is in native image pixels; CSS size and DPR are presentation only.
 */
import type { FrontPlacement } from "./FrontPlacement";
import profile from "../../../api/app/modules/realistic_review_ugc/assets/slot2_hat_grid_v2.json";

export type Point = [number, number];
export type UVBox = [number, number, number, number];
export type ArtworkBounds = { x: number; y: number; width: number; height: number };
export type UvView = "front" | "threeQuarter";
type CanvasSource = HTMLImageElement | HTMLCanvasElement;
type View = (typeof profile.views)[UvView];
const columns = 4;
const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, n));
const interp = (a: number, b: number, t: number) => a + (b - a) * t;
export const UV_PROFILE_ID = profile.profileId;

export function uvProfileView(view: UvView): View {
  return profile.views[view];
}

/** The user's FRONT rectangle is authoritative. Its saved zoom (40–200%)
 * only participates in this ONE conversion from painted pixels to UV.
 */
export function frontUvBox(bounds: ArtworkBounds, front: FrontPlacement, percent: number): UVBox {
  const view = profile.views.front;
  const gridWidth = view.nodes[2][4][0] - view.nodes[2][0][0];
  const gridHeight = view.nodes[4][2][1] - view.nodes[0][2][1];
  const fieldWidth = front.width * view.width, fieldHeight = front.height * view.height;
  const base = Math.min(.95 * fieldWidth / bounds.width, .92 * fieldHeight / bounds.height);
  const zoom = clamp(percent / 100, .4, 2);
  const scale = base * Math.min(zoom,
    fieldWidth * .98 / (bounds.width * base),
    fieldHeight * .98 / (bounds.height * base));
  const centerX = (front.left + front.width / 2) * view.width;
  const centerY = (front.top + front.height / 2) * view.height;
  // Exact saved center: never snap a slightly off-center placement.
  const centerU = (centerX - view.nodes[2][0][0]) / gridWidth;
  const centerV = (centerY - view.nodes[0][2][1]) / gridHeight;
  const halfU = bounds.width * scale / gridWidth / 2;
  const halfV = bounds.height * scale / gridHeight / 2;
  const box: UVBox = [centerU - halfU, centerV - halfV, centerU + halfU, centerV + halfV];
  if (box.some(n => !Number.isFinite(n) || n < -1e-8 || n > 1 + 1e-8)) {
    throw new Error("Saved FRONT placement exceeds the UV mesh. Calibrate its profile nodes; do not refit the 3/4 design.");
  }
  return box;
}

/** Alpha threshold/margin matches the server. No background-color or centroid
 * heuristic, no crop after visibility clipping, no resizing for the 3/4 mask.
 */
export function visibleArtworkBounds(image: CanvasSource): ArtworkBounds {
  const w = image instanceof HTMLImageElement ? image.naturalWidth : image.width;
  const h = image instanceof HTMLImageElement ? image.naturalHeight : image.height;
  if (!w || !h || w * h > 16_000_000) throw new Error("Original artwork dimensions invalid.");
  const canvas = document.createElement("canvas");
  canvas.width = w; canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true });
  if (!ctx) throw new Error("Cannot inspect original artwork alpha.");
  ctx.drawImage(image, 0, 0);
  const rgba = ctx.getImageData(0, 0, w, h).data;
  let left = w, top = h, right = -1, bottom = -1, transparent = false;
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const alpha = rgba[(y * w + x) * 4 + 3];
    if (alpha < 255) transparent = true;
    if (alpha < 24) continue;
    left = Math.min(left, x); top = Math.min(top, y);
    right = Math.max(right, x); bottom = Math.max(bottom, y);
  }
  if (!transparent || right < left || bottom < top) throw new Error("Original design requires a transparent PNG with painted pixels.");
  left = Math.max(0, left - 1); top = Math.max(0, top - 1);
  right = Math.min(w, right + 2); bottom = Math.min(h, bottom + 2);
  return { x: left, y: top, width: right - left, height: bottom - top };
}

export function makeUvTexture(image: CanvasSource, front: FrontPlacement, percent: number) {
  const bounds = visibleArtworkBounds(image);
  const box = frontUvBox(bounds, front, percent);
  const texture = document.createElement("canvas");
  texture.width = 1024;
  texture.height = Math.round(1024 * profile.logicalArea.height / profile.logicalArea.width);
  const ctx = texture.getContext("2d");
  if (!ctx) throw new Error("Cannot create shared UV texture.");
  const x0 = Math.round(box[0] * texture.width), x1 = Math.round(box[2] * texture.width);
  const y0 = Math.round(box[1] * texture.height), y1 = Math.round(box[3] * texture.height);
  ctx.drawImage(image, bounds.x, bounds.y, bounds.width, bounds.height,
    x0, y0, Math.max(1, x1 - x0), Math.max(1, y1 - y0));
  return { texture, box, bounds };
}

/** Piecewise bilinear P_view(u,v) from the profile's JSON control nodes.
 * There is NO additional skew, rotation, barrel/dome or angle-specific fit.
 */
export function projectProfileUv(viewName: UvView, u: number, v: number, width?: number, height?: number): Point {
  const view = uvProfileView(viewName);
  const x = clamp(u, 0, 1) * columns, y = clamp(v, 0, 1) * columns;
  const c = Math.min(columns - 1, Math.floor(x)), r = Math.min(columns - 1, Math.floor(y));
  const a = x - c, b = y - r;
  const p = view.nodes[r][c], q = view.nodes[r][c + 1];
  const s = view.nodes[r + 1][c], t = view.nodes[r + 1][c + 1];
  const point: Point = [
    interp(interp(p[0], q[0], a), interp(s[0], t[0], a), b),
    interp(interp(p[1], q[1], a), interp(s[1], t[1], a), b),
  ];
  return [point[0] * (width ?? view.width) / view.width,
    point[1] * (height ?? view.height) / view.height];
}

function drawTriangle(ctx: CanvasRenderingContext2D, image: CanvasSource,
  source: [Point, Point, Point], dest: [Point, Point, Point]) {
  const [a, b, c] = source, [p, q, r] = dest;
  const x1 = b[0] - a[0], y1 = b[1] - a[1], x2 = c[0] - a[0], y2 = c[1] - a[1];
  const det = x1 * y2 - x2 * y1;
  if (Math.abs(det) < 1e-7) return;
  const dx1 = q[0] - p[0], dy1 = q[1] - p[1], dx2 = r[0] - p[0], dy2 = r[1] - p[1];
  const A = (dx1 * y2 - dx2 * y1) / det, B = (dy1 * y2 - dy2 * y1) / det;
  const C = (dx2 * x1 - dx1 * x2) / det, D = (dy2 * x1 - dy1 * x2) / det;
  const E = p[0] - A * a[0] - C * a[1], F = p[1] - B * a[0] - D * a[1];
  ctx.save(); ctx.beginPath(); ctx.moveTo(...p); ctx.lineTo(...q); ctx.lineTo(...r);
  ctx.closePath(); ctx.clip();
  ctx.setTransform(A, B, C, D, E, F);
  // Bounded source tile only, avoiding thousands of full-atlas draw calls.
  const minX = Math.max(0, Math.floor(Math.min(a[0], b[0], c[0])) - 1);
  const minY = Math.max(0, Math.floor(Math.min(a[1], b[1], c[1])) - 1);
  const maxX = Math.min(image.width, Math.ceil(Math.max(a[0], b[0], c[0])) + 1);
  const maxY = Math.min(image.height, Math.ceil(Math.max(a[1], b[1], c[1])) + 1);
  if (maxX > minX && maxY > minY)
    ctx.drawImage(image, minX, minY, maxX - minX, maxY - minY,
      minX, minY, maxX - minX, maxY - minY);
  ctx.restore();
}

export type SharedUvTexture = ReturnType<typeof makeUvTexture>;
export function renderSharedUvView(canvas: HTMLCanvasElement, source: SharedUvTexture,
  viewName: UvView, width: number, height: number, debug = false) {
  const view = uvProfileView(viewName);
  if (Math.abs((width / height) / (view.width / view.height) - 1) > .15)
    throw new Error("Stock aspect differs from calibrated UV profile.");
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Cannot render UV canvas.");
  ctx.clearRect(0, 0, width, height);
  // Path2D contains coverage, not an opaque black RGB mask. Triangle clips
  // form the independent gridCoverage mask; transparent source makes alphaDesign.
  ctx.save();
  ctx.scale(width / view.width, height / view.height);
  ctx.clip(new Path2D(view.visibleMaskPath));
  ctx.scale(view.width / width, view.height / height);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  const samples = 6;
  for (let r = 0; r < 4 * samples; r++) for (let c = 0; c < 4 * samples; c++) {
    const u0 = c / (4 * samples), u1 = (c + 1) / (4 * samples);
    const v0 = r / (4 * samples), v1 = (r + 1) / (4 * samples);
    const w = source.texture.width, h = source.texture.height;
    const t0: Point = [u0 * w, v0 * h], t1: Point = [u1 * w, v0 * h];
    const t2: Point = [u1 * w, v1 * h], t3: Point = [u0 * w, v1 * h];
    const d0 = projectProfileUv(viewName, u0, v0, width, height);
    const d1 = projectProfileUv(viewName, u1, v0, width, height);
    const d2 = projectProfileUv(viewName, u1, v1, width, height);
    const d3 = projectProfileUv(viewName, u0, v1, width, height);
    drawTriangle(ctx, source.texture, [t0, t1, t2], [d0, d1, d2]);
    drawTriangle(ctx, source.texture, [t0, t2, t3], [d0, d2, d3]);
  }
  ctx.restore();
  if (debug) drawUvDebug(ctx, viewName, source.box, width, height);
}

export function drawUvDebug(ctx: CanvasRenderingContext2D, view: UvView,
  box: UVBox, width: number, height: number) {
  ctx.save();
  ctx.lineWidth = Math.max(1, Math.min(width, height) / 320);
  function strokeLine(u0: number, v0: number, u1: number, v1: number, color: string, dashed = false) {
    ctx.beginPath(); ctx.strokeStyle = color; ctx.setLineDash(dashed ? [6, 4] : []);
    for (let n = 0; n <= 32; n++) {
      const t = n / 32;
      const [x, y] = projectProfileUv(view, interp(u0, u1, t), interp(v0, v1, t), width, height);
      if (n) ctx.lineTo(x, y); else ctx.moveTo(x, y);
    }
    ctx.stroke();
  }
  for (let i = 0; i <= 4; i++) {
    strokeLine(i/4, 0, i/4, 1, "#10a6d5");
    strokeLine(0, i/4, 1, i/4, "#10a6d5");
  }
  // The central UV axis and (when centered) the design axis are the same P_view.
  strokeLine(.5, 0, .5, 1, "#fbbf24");
  strokeLine((box[0] + box[2])/2, box[1], (box[0] + box[2])/2, box[3], "#e040ec", true);
  strokeLine(box[0], box[1], box[2], box[1], "#e040ec");
  strokeLine(box[0], box[3], box[2], box[3], "#e040ec");
  strokeLine(box[0], (box[1] + box[3])/2, box[2], (box[1] + box[3])/2, "#a855f7", true);
  const grid = uvProfileView(view);
  ctx.save();
  ctx.scale(width / grid.width, height / grid.height);
  ctx.strokeStyle = "#ef4444";
  ctx.setLineDash([7, 4]);
  ctx.stroke(new Path2D(grid.visibleMaskPath));
  ctx.restore();
  ctx.restore();
}
