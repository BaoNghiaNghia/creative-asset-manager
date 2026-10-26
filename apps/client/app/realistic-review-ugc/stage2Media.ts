/** Only tenant-authorized RRUGC routes may request compact previews. */
const PRIVATE_IMAGE_PATH = /^\/api\/v1\/realistic-review-ugc\/(?:colorways\/[^/?#]+\/output|source-plans\/[^/?#]+\/image|products-stage2\/ecommerce\/jobs\/[^/?#]+\/output|stage2-jobs\/[^/?#]+\/(?:output|reference-image))$/;
const STOCK_IMAGE_PATH = /^\/rrugc\/blueprint\/stock\/[a-z0-9-]+\/(?:front|side|back)\.(?:jpe?g|png|webp)$/i;
const SOURCE_PLAN_IMAGE_PATH = /^\/api\/v1\/realistic-review-ugc\/source-plans\/[^/?#]+\/image$/;

/** Stage 1-selected Product artwork must use real PNG pixels, never the Drive
 * preview that may flatten its transparent background to opaque black.
 * Only authenticated source-plan URLs are allowed; preserve their revision.
 */
export function productEmbroideryArtworkUrl(src: string, size = 384): string {
  if (!src || !src.startsWith("/") || src.startsWith("//")) return src;
  const [path] = src.split("?");
  if (!SOURCE_PLAN_IMAGE_PATH.test(path)) return src;
  const params = new URLSearchParams(src.includes("?") ? src.slice(path.length + 1) : "");
  params.set("thumbnail", "true");
  params.set("size", String(size));
  params.set("artwork", "stage1-thread-geometry-v1");
  return path + "?" + params.toString();
}

/** Original product artwork remains a visible fabric photograph; only the
 * hat-placement canvas uses the transient verified thread view above.
 */
export function productFabricArtworkUrl(src: string, size = 384): string {
  if (!src || !src.startsWith("/") || src.startsWith("//")) return src;
  const [path] = src.split("?");
  if (!SOURCE_PLAN_IMAGE_PATH.test(path)) return src;
  const params = new URLSearchParams(src.includes("?") ? src.slice(path.length + 1) : "");
  params.set("thumbnail", "true");
  params.set("size", String(size));
  params.delete("artwork");
  return path + "?" + params.toString();
}

export function stage2ThumbnailUrl(src: string, size: 128 | 192 | 256 | 512 | 1024): string {
  if (!src || !src.startsWith("/") || src.startsWith("//")) return src;
  const [path] = src.split("?");
  if (!PRIVATE_IMAGE_PATH.test(path)) return src;
  const params = new URLSearchParams(src.slice(path.length + (src.includes("?") ? 1 : 0)));
  params.set("thumbnail", "true");
  params.set("size", String(size));
  return path + "?" + params.toString();
}

/** Only the full-image modal may remove thumbnail parameters. Preserve revisions,
 * the requested Ecommerce version and reference indices for correct originals.
 */
export function stage2OriginalUrl(src: string): string {
  if (!src || !src.startsWith("/") || src.startsWith("//")) return src;
  const [path] = src.split("?");
  if (!PRIVATE_IMAGE_PATH.test(path) || !src.includes("?")) return src;
  const params = new URLSearchParams(src.slice(path.length + 1));
  params.delete("thumbnail");
  params.delete("size");
  const query = params.toString();
  return path + (query ? "?" + query : "");
}

/** Pinterest provides genuinely resized variants at its own image origin.
 * Do not proxy or rewrite private images or arbitrary third-party hosts.
 */
export function stage2PinterestThumbnailUrl(src: string): string {
  try {
    const url = new URL(src);
    if (url.protocol !== "https:" || url.hostname !== "i.pinimg.com" ||
        url.username || url.password) return src;
    if (!/^\/(?:originals|236x|474x|564x|736x)\/[a-z0-9/_-]+\.(?:jpe?g|png|webp)$/i.test(url.pathname)) return src;
    url.pathname = url.pathname.replace(/^\/(?:originals|236x|474x|564x|736x)\//, "/236x/");
    return url.toString();
  } catch {
    return src;
  }
}

/** Official bundled stock is public; private job/Drive paths must never be remapped. */
export function publicHatStockUrl(
  src: string,
  cdnOrigin = import.meta.env.VITE_RRUGC_PUBLIC_STOCK_CDN_ORIGIN as string | undefined,
): string {
  if (!STOCK_IMAGE_PATH.test(src) || !cdnOrigin) return src;
  try {
    const url = new URL(cdnOrigin);
    if (url.protocol !== "https:" || url.username || url.password ||
        url.search || url.hash || url.pathname !== "/") return src;
    return url.origin + src;
  } catch {
    return src;
  }
}
