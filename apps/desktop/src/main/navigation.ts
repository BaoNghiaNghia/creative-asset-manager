export type NavigationDisposition = "internal" | "external" | "rejected";

const LOCALHOST_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);
export const PACKAGED_CAM_ORIGIN = "https://creative-assets.ddns.net";

function parseUrl(value: string): URL | undefined {
  try {
    return new URL(value);
  } catch {
    return undefined;
  }
}

function isLocalhost(url: URL): boolean {
  return LOCALHOST_HOSTS.has(url.hostname);
}

export function resolveDesktopUrl(
  configuredUrl: string | undefined,
  isPackaged: boolean,
): URL {
  const value = configuredUrl?.trim() ||
    (isPackaged ? PACKAGED_CAM_ORIGIN : "http://localhost:5173");
  const url = parseUrl(value);

  if (!url) {
    throw new Error("CAM_DESKTOP_URL must be an absolute URL.");
  }

  if (url.protocol === "https:") {
    return url;
  }

  if (!isPackaged && url.protocol === "http:" && isLocalhost(url)) {
    return url;
  }

  throw new Error(
    "CAM_DESKTOP_URL must use HTTPS, except http://localhost during development.",
  );
}

export function classifyNavigation(
  target: string,
  camUrl: URL,
): NavigationDisposition {
  const url = parseUrl(target);

  if (!url) {
    return "rejected";
  }

  if (url.origin === camUrl.origin) {
    return "internal";
  }

  return url.protocol === "https:" ? "external" : "rejected";
}

export function isInternalCamUrl(target: string, camUrl: URL): boolean {
  return classifyNavigation(target, camUrl) === "internal";
}
