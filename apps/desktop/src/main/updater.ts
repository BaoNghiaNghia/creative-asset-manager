import { app, BrowserWindow, ipcMain } from "electron";
import { autoUpdater } from "electron-updater";
import type { DesktopUpdateState } from "../shared/types";
import { isDesktopRendererReady } from "./window";

const DEFAULT_UPDATE_URL = "https://creative-assets.ddns.net/desktop-updates/windows/";
const POST_RENDERER_CHECK_DELAY_MS = 3_000;
const RENDERER_READY_WAIT_MS = 30_000;
const RENDERER_READY_POLL_MS = 250;
const PERIODIC_CHECK_INTERVAL_MS = 15 * 60 * 1000;
const MIN_BACKGROUND_CHECK_GAP_MS = 5 * 60 * 1000;

let state: DesktopUpdateState = {
  status: "idle",
  currentVersion: app.getVersion(),
};
let handlersRegistered = false;
let updaterConfigured = false;
let updateCheck: Promise<void> | undefined;
let lastBackgroundCheckAt = 0;
let periodicCheckTimer: ReturnType<typeof setInterval> | undefined;

function errorMessage(error: unknown): string {
  const value = error instanceof Error ? error.message : String(error || "Update failed");
  return value.replace(/\s+/g, " ").trim().slice(0, 240);
}

function publish(window: BrowserWindow | undefined, patch: Partial<DesktopUpdateState>): DesktopUpdateState {
  state = { ...state, ...patch, currentVersion: app.getVersion() };
  if (window && !window.isDestroyed()) {
    window.webContents.send("desktop:update:state", state);
  }
  return state;
}

export function desktopUpdateSnapshot(): DesktopUpdateState {
  return { ...state };
}

async function waitForRendererReady(
  getWindow: () => BrowserWindow | undefined,
  timeoutMs = RENDERER_READY_WAIT_MS,
): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await isDesktopRendererReady(getWindow())) return true;
    await new Promise(resolve => setTimeout(resolve, RENDERER_READY_POLL_MS));
  }
  return false;
}

async function runUpdateCheck(
  getWindow: () => BrowserWindow | undefined,
  options: { force?: boolean } = {},
): Promise<void> {
  if (!app.isPackaged || !updaterConfigured) return;
  if (!options.force && !(await isDesktopRendererReady(getWindow()))) return;
  if (!options.force && Date.now() - lastBackgroundCheckAt < MIN_BACKGROUND_CHECK_GAP_MS) return;
  if (updateCheck) return updateCheck;
  lastBackgroundCheckAt = Date.now();

  updateCheck = (async () => {
    try {
      publish(getWindow(), { status: "checking", message: undefined });
      await autoUpdater.checkForUpdates();
    } catch (error) {
      publish(getWindow(), { status: "error", message: errorMessage(error) });
    } finally {
      updateCheck = undefined;
    }
  })();
  return updateCheck;
}

export function registerDesktopUpdater(getWindow: () => BrowserWindow | undefined): void {
  if (!handlersRegistered) {
    ipcMain.handle("desktop:update:get-state", () => desktopUpdateSnapshot());
    ipcMain.handle("desktop:update:check", async () => {
      await runUpdateCheck(getWindow, { force: true });
      return desktopUpdateSnapshot();
    });
    ipcMain.handle("desktop:update:install", () => {
      if (state.status !== "ready") return false;
      autoUpdater.quitAndInstall(true, true);
      return true;
    });
    handlersRegistered = true;
  }

  if (!app.isPackaged) {
    publish(getWindow(), {
      status: "disabled",
      message: "Automatic updates are enabled in packaged desktop builds.",
    });
    return;
  }
  if (updaterConfigured) return;

  updaterConfigured = true;
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowDowngrade = false;
  autoUpdater.allowPrerelease = false;
  autoUpdater.setFeedURL({
    provider: "generic",
    url: process.env.CAM_DESKTOP_UPDATE_URL?.trim() || DEFAULT_UPDATE_URL,
    channel: "latest",
  });

  autoUpdater.on("checking-for-update", () => {
    publish(getWindow(), { status: "checking", message: undefined });
  });
  autoUpdater.on("update-available", info => {
    publish(getWindow(), {
      status: "available",
      availableVersion: info.version,
      percent: 0,
      message: undefined,
    });
  });
  autoUpdater.on("download-progress", progress => {
    publish(getWindow(), {
      status: "downloading",
      percent: Math.max(0, Math.min(100, progress.percent)),
    });
  });
  autoUpdater.on("update-not-available", () => {
    publish(getWindow(), {
      status: "up-to-date",
      availableVersion: undefined,
      percent: undefined,
      message: undefined,
    });
  });
  autoUpdater.on("update-downloaded", info => {
    publish(getWindow(), {
      status: "ready",
      availableVersion: info.version,
      percent: 100,
      message: undefined,
    });
  });
  autoUpdater.on("error", error => {
    publish(getWindow(), { status: "error", message: errorMessage(error) });
  });

  // Do not compete with DNS/TLS, React boot, cache recovery or media
  // initialization. The updater starts only after the production renderer has
  // explicitly confirmed that React committed successfully.
  void waitForRendererReady(getWindow).then(ready => {
    if (!ready) return;
    const startupTimer = setTimeout(() => {
      void runUpdateCheck(getWindow);
    }, POST_RENDERER_CHECK_DELAY_MS);
    startupTimer.unref?.();
  });

  if (!periodicCheckTimer) {
    periodicCheckTimer = setInterval(() => {
      void runUpdateCheck(getWindow);
    }, PERIODIC_CHECK_INTERVAL_MS);
    periodicCheckTimer.unref?.();
  }
}
