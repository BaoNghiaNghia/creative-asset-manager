import { createHash, randomBytes } from "node:crypto";
import { app, shell, type BrowserWindow } from "electron";
import { resolveDesktopUrl } from "./navigation";

const PROVIDERS = new Set(["google", "microsoft"]);
const LAUNCH_TOKEN_RE = /^[A-Za-z0-9_-]{32,256}$/;

export type DesktopOAuthProvider = "google" | "microsoft";
export type DesktopOAuthIntent = "google_drive_connect" | "onedrive_connect";

export function createDesktopInstanceNonce(): string {
  return randomBytes(32).toString("base64url");
}

export function instanceBinding(nonce: string): string {
  return createHash("sha256").update(nonce, "utf8").digest("hex");
}

export function isDesktopOAuthProvider(value: unknown): value is DesktopOAuthProvider {
  return typeof value === "string" && PROVIDERS.has(value);
}

export function isExpectedLaunchUrl(value: string, camUrl: URL): boolean {
  try {
    const url = new URL(value);
    const prefix = "/api/v1/desktop/oauth/launch/";
    const token = url.pathname.startsWith(prefix)
      ? url.pathname.slice(prefix.length)
      : "";
    return (
      url.origin === camUrl.origin &&
      !url.search &&
      !url.hash &&
      LAUNCH_TOKEN_RE.test(token)
    );
  } catch {
    return false;
  }
}

function apiUrl(camUrl: URL, path: string): string {
  return new URL(path, camUrl).toString();
}

export async function beginDesktopOAuth(
  window: BrowserWindow,
  nonce: string,
  request: { provider?: DesktopOAuthProvider; intent?: DesktopOAuthIntent; externalSourceId?: string },
): Promise<void> {
  if ((!request.intent && !isDesktopOAuthProvider(request.provider)) || (request.intent && !["google_drive_connect", "onedrive_connect"].includes(request.intent))) {
    throw new Error("Unsupported OAuth request.");
  }
  const camUrl = resolveDesktopUrl(process.env.CAM_DESKTOP_URL, app.isPackaged);
  const response = await window.webContents.session.fetch(apiUrl(camUrl, "/api/v1/desktop/oauth/start"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...(request.intent ? { intent: request.intent, external_source_id: request.externalSourceId } : { provider: request.provider }),
      desktop_instance_binding: instanceBinding(nonce),
    }),
  });
  if (!response.ok) {
    throw new Error("Desktop sign-in is unavailable.");
  }
  const body = (await response.json()) as { launch_url?: unknown };
  if (typeof body.launch_url !== "string" || !isExpectedLaunchUrl(body.launch_url, camUrl)) {
    throw new Error("Invalid desktop OAuth launch URL.");
  }
  await shell.openExternal(body.launch_url);
}

export async function redeemDesktopOAuth(
  window: BrowserWindow,
  nonce: string,
  ticket: string,
): Promise<void> {
  const camUrl = resolveDesktopUrl(process.env.CAM_DESKTOP_URL, app.isPackaged);
  const response = await window.webContents.session.fetch(apiUrl(camUrl, "/api/v1/desktop/oauth/redeem"), {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ticket, desktop_instance_nonce: nonce }),
  });
  if (!response.ok) {
    throw new Error("Desktop sign-in could not be completed.");
  }
  window.webContents.send("desktop-auth-complete");
}
