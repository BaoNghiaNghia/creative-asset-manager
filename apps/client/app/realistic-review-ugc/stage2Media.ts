/** Only tenant-authorized RRUGC routes may request compact previews. */
const PRIVATE_IMAGE_PATH = /^\/api\/v1\/realistic-review-ugc\/(?:colorways\/[^/?#]+\/output|source-plans\/[^/?#]+\/image)$/;
const STOCK_IMAGE_PATH = /^\/rrugc\/blueprint\/stock\/[a-z0-9-]+\/(?:front|side|back)\.(?:jpe?g|png|webp)$/i;

export function stage2ThumbnailUrl(src: string, size: 128 | 256 | 512 | 1024): string {
  if (!src || !src.startsWith("/") || src.startsWith("//")) return src;
  const [path] = src.split("?");
  if (!PRIVATE_IMAGE_PATH.test(path)) return src;
  const params = new URLSearchParams(src.slice(path.length + (src.includes("?") ? 1 : 0)));
  params.set("thumbnail", "true");
  params.set("size", String(size));
  return path + "?" + params.toString();
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
