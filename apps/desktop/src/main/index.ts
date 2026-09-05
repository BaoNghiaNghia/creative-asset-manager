import { app, BrowserWindow, ipcMain } from "electron";
import { createMainWindow } from "./window";
import {
  beginDesktopOAuth,
  createDesktopInstanceNonce,
  isDesktopOAuthProvider,
  redeemDesktopOAuth,
} from "./oauth";
import { findOAuthDeepLink } from "./protocol";

let mainWindow: BrowserWindow | undefined;
const desktopInstanceNonce = createDesktopInstanceNonce();

function focusWindow(): void {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
}

async function processDeepLink(argumentsList: readonly string[]): Promise<void> {
  const handoff = findOAuthDeepLink(argumentsList);
  if (!handoff || !mainWindow) return;
  focusWindow();
  try {
    await redeemDesktopOAuth(mainWindow, desktopInstanceNonce, handoff.ticket);
  } catch {
    mainWindow.webContents.send("desktop-auth-complete");
  }
}

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", (_event, argumentsList) => {
    void processDeepLink(argumentsList);
  });

  app.whenReady().then(() => {
    if (process.defaultApp && process.argv.length >= 2) {
      app.setAsDefaultProtocolClient("cam", process.execPath, [process.argv[1]]);
    } else {
      app.setAsDefaultProtocolClient("cam");
    }
    mainWindow = createMainWindow();
    ipcMain.handle("desktop:oauth:begin", async (_event, request: unknown) => {
      if (!mainWindow || !request || typeof request !== "object") {
        throw new Error("Desktop sign-in is unavailable.");
      }
      const provider = (request as { provider?: unknown }).provider;
      if (!isDesktopOAuthProvider(provider)) {
        throw new Error("Unsupported OAuth provider.");
      }
      await beginDesktopOAuth(mainWindow, desktopInstanceNonce, provider);
    });
    void processDeepLink(process.argv);

    app.on("activate", () => {
      if (!mainWindow || mainWindow.isDestroyed()) mainWindow = createMainWindow();
    });
  });
}

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
