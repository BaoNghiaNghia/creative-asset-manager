import { app, BrowserWindow, ipcMain } from "electron";
import { autoUpdater } from "electron-updater";
import type { DesktopUpdateState } from "../shared/types";

const DEFAULT_UPDATE_URL = "https://creative-assets.ddns.net/desktop-updates/windows/";
const STARTUP_CHECK_DELAY_MS = 4_000;

let state: DesktopUpdateState = {
  status: "idle",
  currentVersion: app.getVersion(),
};
let handlersRegistered = false;
let updaterConfigured = false;

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

export function registerDesktopUpdater(getWindow: () => BrowserWindow | undefined): void {
  if (!handlersRegistered) {
    ipcMain.handle("desktop:update:get-state", () => desktopUpdateSnapshot());
    ipcMain.handle("desktop:update:check", async () => {
      if (!app.isPackaged || !updaterConfigured) return desktopUpdateSnapshot();
      try {
        publish(getWindow(), { status: "checking", message: undefined });
        await autoUpdater.checkForUpdates();
      } catch (error) {
        publish(getWindow(), { status: "error", message: errorMessage(error) });
      }
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

  setTimeout(() => {
    publish(getWindow(), { status: "checking", message: undefined });
    void autoUpdater.checkForUpdates().catch(error => {
      publish(getWindow(), { status: "error", message: errorMessage(error) });
    });
  }, STARTUP_CHECK_DELAY_MS);
}
