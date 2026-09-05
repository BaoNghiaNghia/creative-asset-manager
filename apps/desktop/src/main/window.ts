import { app, BrowserWindow, shell } from "electron";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { classifyNavigation, resolveDesktopUrl } from "./navigation";

const currentDirectory = dirname(fileURLToPath(import.meta.url));

function openExternalIfValid(target: string, camUrl: URL): void {
  if (classifyNavigation(target, camUrl) === "external") {
    void shell.openExternal(target);
  }
}

export function createMainWindow(): BrowserWindow {
  const camUrl = resolveDesktopUrl(process.env.CAM_DESKTOP_URL, app.isPackaged);
  const window = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 700,
    show: false,
    webPreferences: {
      preload: join(currentDirectory, "../preload/index.mjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
    },
  });

  const session = window.webContents.session;
  session.setPermissionRequestHandler((_contents, _permission, callback) => {
    callback(false);
  });
  session.setPermissionCheckHandler(() => false);

  window.webContents.setWindowOpenHandler(({ url }) => {
    const disposition = classifyNavigation(url, camUrl);

    if (disposition === "internal") {
      void window.loadURL(url);
    } else {
      openExternalIfValid(url, camUrl);
    }

    return { action: "deny" };
  });

  const blockUnexpectedNavigation = (event: Electron.Event, url: string) => {
    const disposition = classifyNavigation(url, camUrl);

    if (disposition !== "internal") {
      event.preventDefault();
      openExternalIfValid(url, camUrl);
    }
  };

  window.webContents.on("will-navigate", blockUnexpectedNavigation);
  window.webContents.on("will-redirect", blockUnexpectedNavigation);
  window.once("ready-to-show", () => window.show());
  void window.loadURL(camUrl.toString());

  return window;
}
