/**
 * Preview stays cheap for fast color/design navigation. The detail viewer may
 * use up to 7M *source* pixels to retain genuine stitches at high zoom.
 * Never enlarge a low-resolution master: that cannot restore missing threads.
 */
export type EmbroideryRasterQuality = "tile" | "preview" | "detail";

export function chooseEmbroideryRasterSize(
  naturalWidth: number, naturalHeight: number, quality: EmbroideryRasterQuality,
): { width: number; height: number } {
  if (!Number.isFinite(naturalWidth) || !Number.isFinite(naturalHeight) ||
      naturalWidth <= 0 || naturalHeight <= 0) return { width: 1, height: 1 };
  const longest = quality === "detail" ? 3200 : quality === "tile" ? 384 : 1800;
  const pixelBudget = quality === "detail" ? 7_000_000 : quality === "tile" ? 147_456 : 3_240_000;
  const scale = Math.min(
    1,
    longest / Math.max(naturalWidth, naturalHeight),
    Math.sqrt(pixelBudget / (naturalWidth * naturalHeight)),
  );
  let width = Math.max(1, Math.round(naturalWidth * scale));
  let height = Math.max(1, Math.round(naturalHeight * scale));
  if (width * height > pixelBudget) {
    const safeScale = Math.sqrt(pixelBudget / (width * height));
    width = Math.max(1, Math.floor(width * safeScale));
    height = Math.max(1, Math.floor(height * safeScale));
  }
  return { width, height };
}
