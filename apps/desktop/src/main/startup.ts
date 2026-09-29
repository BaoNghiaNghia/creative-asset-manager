import { readFile, rm, writeFile } from "node:fs/promises";
import { join } from "node:path";

export const MAX_STARTUP_NAVIGATION_RETRIES = 2;

export function startupRetryDelayMs(attempt: number): number {
  return Math.min(4_000, 750 * 2 ** Math.max(0, attempt));
}

export function shouldRetryMainFrameLoad(
  isMainFrame: boolean,
  errorCode: number,
  attempt: number,
): boolean {
  // ERR_ABORTED (-3) is expected when a newer navigation supersedes an older one.
  return isMainFrame && errorCode !== -3 && attempt < MAX_STARTUP_NAVIGATION_RETRIES;
}

export function shouldRefreshTransientCaches(
  previousVersion: string | undefined,
  currentVersion: string,
): boolean {
  return !!currentVersion && previousVersion !== currentVersion;
}

type PrepareTransientCacheOptions = {
  userDataPath: string;
  currentVersion: string;
  clearHttpCache: () => Promise<void>;
  clearCodeCaches: () => Promise<void>;
};

export async function prepareTransientCachesOnVersionChange(
  options: PrepareTransientCacheOptions,
): Promise<boolean> {
  const markerPath = join(options.userDataPath, ".cam-desktop-version");
  let previousVersion: string | undefined;
  try {
    previousVersion = (await readFile(markerPath, "utf8")).trim() || undefined;
  } catch {
    previousVersion = undefined;
  }

  if (!shouldRefreshTransientCaches(previousVersion, options.currentVersion)) return false;

  // Only transient caches are cleared. Cookies, Local Storage, IndexedDB and
  // authentication/session data are deliberately preserved.
  await Promise.allSettled([
    options.clearHttpCache(),
    options.clearCodeCaches(),
    rm(join(options.userDataPath, "GPUCache"), { recursive: true, force: true }),
    rm(join(options.userDataPath, "DawnCache"), { recursive: true, force: true }),
    rm(join(options.userDataPath, "GrShaderCache"), { recursive: true, force: true }),
    rm(join(options.userDataPath, "ShaderCache"), { recursive: true, force: true }),
  ]);
  await writeFile(markerPath, options.currentVersion, "utf8");
  return true;
}
