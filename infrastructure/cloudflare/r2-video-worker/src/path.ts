/** Only immutable original and derived playback video key shapes are readable. */
const VIDEO_PATH = /^\/video-cache\/([A-Za-z0-9][A-Za-z0-9_-]{0,254})\/([0-9a-f]{64})\/(?:original|playback\.mp4)$/;

export function cacheKeyFromPath(pathname: string): string | null {
  if (!VIDEO_PATH.test(pathname)) return null;
  // The strict ASCII expression rejects percent encoding, traversal, control
  // characters, backslashes, duplicate separators and alternate extensions.
  return pathname.slice(1);
}
