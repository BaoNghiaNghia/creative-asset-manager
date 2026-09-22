import { app, BrowserWindow, dialog, ipcMain, nativeImage } from "electron";
import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { createMainWindow } from "./window";
import {
  beginDesktopOAuth,
  createDesktopInstanceNonce,
  isDesktopOAuthProvider,
  redeemDesktopOAuth,
} from "./oauth";
import { findOAuthDeepLink } from "./protocol";
import { IngestionService, type Destination } from "./ingestion";
import { createUploadTransport } from "./uploadTransport";
import { NativeDragService, type NativeDragAssetRequest } from "./nativeDrag";

let mainWindow: BrowserWindow | undefined;
let ingestion: IngestionService | undefined;
let nativeDrag: NativeDragService | undefined;
const desktopInstanceNonce = createDesktopInstanceNonce();
const FALLBACK_DRAG_ICON = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAQAAAC1+jfqAAAAE0lEQVR42mNkYGD4z0AEYBxVSFUAANWfAf8nqQzRAAAAAElFTkSuQmCC";

function focusWindow(): void {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
}
function validDestination(value: unknown): Destination | undefined {
  if (!value || typeof value !== "object") return undefined;
  const input = value as Partial<Destination>;
  if (input.provider !== "google-drive" || typeof input.parentId !== "string" || !input.parentId || input.parentId.length > 2048) return undefined;
  if (input.externalSourceId !== undefined && (typeof input.externalSourceId !== "string" || input.externalSourceId.length > 64)) return undefined;
  return { parentId: input.parentId, provider: "google-drive", externalSourceId: input.externalSourceId };
}
function service(): IngestionService {
  if (!mainWindow) throw new Error("Desktop ingestion is unavailable.");
  if (!ingestion) {
    ingestion = new IngestionService(
      createUploadTransport(mainWindow.webContents.session, () => mainWindow?.webContents.getURL() || ""),
      jobId => mainWindow?.webContents.send("desktop:ingestion:progress", ingestion?.snapshot(jobId)),
    );
  }
  return ingestion;
}

function nativeDragService(): NativeDragService {
  if (!mainWindow) throw new Error("Desktop native drag is unavailable.");
  if (!nativeDrag) {
    nativeDrag = new NativeDragService(
      mainWindow.webContents.session,
      () => mainWindow?.webContents.getURL() || "",
      join(app.getPath("temp"), "creative-asset-manager", "drag-cache"),
    );
  }
  return nativeDrag;
}

function nativeDragItems(value: unknown): NativeDragAssetRequest[] {
  if (!Array.isArray(value) || value.length < 1 || value.length > 100) {
    throw new Error("Unsupported native drag request.");
  }
  return value as NativeDragAssetRequest[];
}

async function dragIcon(iconPath?: string) {
  if (iconPath) {
    try {
      const image = nativeImage.createFromBuffer(await readFile(iconPath));
      if (!image.isEmpty()) return image.resize({ width: 64, height: 64, quality: "good" });
    } catch {
      // Fall back to a tiny embedded image if provider thumbnails are unavailable.
    }
  }
  return nativeImage.createFromDataURL(FALLBACK_DRAG_ICON);
}

function registerNativeDragIpc(): void {
  ipcMain.handle("desktop:native-drag:prepare", async (event, value: unknown) => {
    if (!mainWindow || event.sender !== mainWindow.webContents) {
      throw new Error("Unsupported native drag request.");
    }
    const prepared = await nativeDragService().prepare(
      nativeDragItems(value),
      { revalidateBeforeUse: false },
    );
    return {
      ready: true,
      count: prepared.files.length,
      cacheHits: prepared.cacheHits,
      cacheMisses: prepared.cacheMisses,
      totalBytes: prepared.totalBytes,
      downloadedBytes: prepared.downloadedBytes,
    };
  });
  ipcMain.handle("desktop:native-drag:start", async (event, value: unknown) => {
    if (!mainWindow || event.sender !== mainWindow.webContents) {
      throw new Error("Unsupported native drag request.");
    }
    const prepared = await nativeDragService().prepare(nativeDragItems(value));
    if (!prepared.files.length) throw new Error("No original files are available for drag.");
    event.sender.startDrag({
      file: prepared.files[0],
      files: prepared.files,
      icon: await dragIcon(prepared.iconPath),
    });
    return {
      started: true,
      count: prepared.files.length,
      cacheHits: prepared.cacheHits,
      cacheMisses: prepared.cacheMisses,
      totalBytes: prepared.totalBytes,
      downloadedBytes: prepared.downloadedBytes,
    };
  });
}
async function processDeepLink(argumentsList: readonly string[]): Promise<void> {
  const handoff = findOAuthDeepLink(argumentsList);
  if (!handoff || !mainWindow) return;
  focusWindow();
  try { await redeemDesktopOAuth(mainWindow, desktopInstanceNonce, handoff.ticket); }
  catch { mainWindow.webContents.send("desktop-auth-complete"); }
}
function registerIngestionIpc(): void {
  ipcMain.handle("desktop:ingestion:drop", async (_event, paths: unknown, destination: unknown) => {
    const target = validDestination(destination);
    if (!target || !Array.isArray(paths) || paths.length < 1 || paths.length > 100) throw new Error("Unsupported desktop ingestion request.");
    if (!paths.every(value => typeof value === "string" && value.length > 0 && value.length < 32768)) throw new Error("Unsupported desktop ingestion request.");
    return service().ingestRoots(paths as string[], target);
  });
  ipcMain.handle("desktop:ingestion:choose-folders", async (_event, destination: unknown) => {
    const target = validDestination(destination);
    if (!target || !mainWindow) throw new Error("Unsupported desktop ingestion request.");
    const choice = await dialog.showOpenDialog(mainWindow, { properties: ["openDirectory", "multiSelections"] });
    if (choice.canceled || !choice.filePaths.length) return undefined;
    return service().ingestRoots(choice.filePaths, target);
  });
  ipcMain.handle("desktop:ingestion:snapshot", (_event, jobId: unknown) => {
    if (typeof jobId !== "string") throw new Error("Unsupported desktop ingestion request.");
    return service().snapshot(jobId);
  });
  for (const action of ["pause", "resume", "cancel"] as const) {
    ipcMain.handle(`desktop:ingestion:${action}`, (_event, jobId: unknown) => {
      if (typeof jobId !== "string") throw new Error("Unsupported desktop ingestion request.");
      return service()[action](jobId);
    });
  }
  ipcMain.handle("desktop:ingestion:retry", (_event, jobId: unknown, itemId: unknown) => {
    if (typeof jobId !== "string" || typeof itemId !== "string") throw new Error("Unsupported desktop ingestion request.");
    return service().retry(jobId, itemId);
  });
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", (_event, argumentsList) => { void processDeepLink(argumentsList); });
  app.whenReady().then(() => {
    if (process.defaultApp && process.argv.length >= 2) app.setAsDefaultProtocolClient("cam", process.execPath, [process.argv[1]]);
    else app.setAsDefaultProtocolClient("cam");
    mainWindow = createMainWindow();
    registerIngestionIpc();
    registerNativeDragIpc();
    ipcMain.handle("desktop:oauth:begin", async (_event, request: unknown) => {
      if (!mainWindow || !request || typeof request !== "object") throw new Error("Desktop sign-in is unavailable.");
      const oauthRequest = request as { provider?: "google" | "microsoft"; intent?: "google_drive_connect" | "onedrive_connect" | "onedrive_personal_connect" | "onedrive_work_connect"; externalSourceId?: string };
      if (!isDesktopOAuthProvider(oauthRequest.provider) && !oauthRequest.intent) throw new Error("Unsupported OAuth provider.");
      await beginDesktopOAuth(mainWindow, desktopInstanceNonce, oauthRequest);
    });
    void processDeepLink(process.argv);
    app.on("activate", () => { if (!mainWindow || mainWindow.isDestroyed()) mainWindow = createMainWindow(); });
  });
}
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); });
