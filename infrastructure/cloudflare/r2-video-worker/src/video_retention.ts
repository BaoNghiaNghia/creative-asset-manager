/** Strict R2 video retention: 30 days from the object's upload timestamp. */
export const VIDEO_RETENTION_SECONDS = 30 * 24 * 60 * 60;
export const VIDEO_EDGE_MAX_AGE_SECONDS = 60 * 60;

export function videoExpirySeconds(uploaded: Date | undefined, nowSeconds: number): number | null {
  if (!(uploaded instanceof Date) || !Number.isFinite(nowSeconds)) return null;
  const ms = uploaded.getTime();
  if (!Number.isFinite(ms)) return null;
  const uploadedSeconds = Math.floor(ms / 1000);
  // Fail closed for an implausibly future-dated object.
  if (uploadedSeconds > nowSeconds + 300) return null;
  return uploadedSeconds + VIDEO_RETENTION_SECONDS;
}

export function internalVideoCacheTtl(deadline: number, nowSeconds: number): number {
  if (!Number.isSafeInteger(deadline) || !Number.isSafeInteger(nowSeconds)) return 0;
  return Math.max(0, Math.min(VIDEO_EDGE_MAX_AGE_SECONDS, deadline - nowSeconds));
}

export function cachedVideoStillValid(response: Response, nowSeconds: number): boolean {
  const raw = response.headers.get("X-CAM-Video-Expires-At");
  if (!raw || !/^[1-9][0-9]{0,12}$/.test(raw)) return false;
  const deadline = Number(raw);
  return response.status === 200 && Number.isSafeInteger(deadline) &&
    deadline > nowSeconds && deadline <= nowSeconds + VIDEO_RETENTION_SECONDS;
}
