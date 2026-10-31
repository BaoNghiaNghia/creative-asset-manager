/** Lightweight Stage 1 thumbnails; full-resolution assets are fetched only in viewers/downloads. */
export function stage1OutputThumbnailUrl(url: string, size: number): string {
  const bounded = Math.max(128, Math.min(1024, Math.round(size)));
  const separator = url.includes("?") ? "&" : "?";
  return url + separator + "thumbnail=true&size=" + bounded + "&preview_format=webp";
}
