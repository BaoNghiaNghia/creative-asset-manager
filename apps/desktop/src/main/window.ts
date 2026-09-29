import { app, BrowserWindow, shell } from "electron";
import { appendFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { classifyNavigation, resolveDesktopUrl } from "./navigation";
import {
  MAX_STARTUP_NAVIGATION_RETRIES,
  prepareTransientCachesOnVersionChange,
  shouldRetryMainFrameLoad,
  startupRetryDelayMs,
} from "./startup";

const currentDirectory = dirname(fileURLToPath(import.meta.url));
const startupStartedAt = Date.now();
const RENDERER_READY_TIMEOUT_MS = 8_000;
const UNRESPONSIVE_RECOVERY_DELAY_MS = 3_000;

function startupLog(stage: string, details?: Record<string, unknown>): void {
  const payload = {
    at: new Date().toISOString(),
    elapsedMs: Date.now() - startupStartedAt,
    version: app.getVersion(),
    stage,
    ...(details || {}),
  };
  const line = JSON.stringify(payload);
  console.info("[cam-desktop-startup]", line);
  try {
    void appendFile(join(app.getPath("userData"), "desktop-startup.log"), line + "\n", "utf8").catch(() => undefined);
  } catch {
    // Logging must never delay or break startup.
  }
}

function htmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function shellDataUrl(kind: "loading" | "error", camUrl: URL, detail?: string): string {
  const loading = kind === "loading";
  const title = loading ? "Opening Creative Asset Manager" : "Creative Asset Manager couldn’t load";
  const subtitle = loading
    ? "Preparing your workspace…"
    : detail || "We’ll retry with a fresh cache. You can also retry now.";
  const action = loading
    ? ""
    : `<a class="retry" href="${htmlEscape(camUrl.toString())}">Retry now</a>`;
  const html = `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="color-scheme" content="light">
<title>Creative Asset Manager</title>
<style>
  *{box-sizing:border-box}
  html,body{width:100%;height:100%;margin:0}
  body{display:grid;place-items:center;background:#f5f7fb;color:#26364d;font-family:Inter,Segoe UI,Arial,sans-serif}
  .shell{display:grid;justify-items:center;gap:12px;padding:28px;text-align:center}
  .mark{display:grid;place-items:center;width:44px;height:44px;border:1px solid #dbe4f2;border-radius:14px;background:#fff;box-shadow:0 10px 30px rgb(39 64 110 / 10%);color:#3566d6;font-size:13px;font-weight:800;letter-spacing:.04em}
  .spinner{width:22px;height:22px;border:2px solid #dce5f4;border-top-color:#3566d6;border-radius:50%;animation:spin .85s linear infinite}
  h1{margin:2px 0 0;font-size:15px;font-weight:750;letter-spacing:-.01em}
  p{max-width:390px;margin:0;color:#7a879a;font-size:11px;line-height:1.55}
  .retry{margin-top:5px;padding:8px 13px;border-radius:9px;background:#315fd8;color:#fff;text-decoration:none;font-size:11px;font-weight:750}
  @keyframes spin{to{transform:rotate(360deg)}}
</style>
</head>
<body>
  <main class="shell">
    <div class="mark">CAM</div>
    ${loading ? '<div class="spinner" aria-hidden="true"></div>' : ""}
    <h1>${htmlEscape(title)}</h1>
    <p>${htmlEscape(subtitle)}</p>
    ${action}
  </main>
</body>
</html>`;
  return "data:text/html;charset=utf-8," + encodeURIComponent(html);
}

function openExternalIfValid(target: string, camUrl: URL): void {
  if (classifyNavigation(target, camUrl) === "external") {
    void shell.openExternal(target);
  }
}

export async function isDesktopRendererReady(window: BrowserWindow | undefined): Promise<boolean> {
  if (!window || window.isDestroyed() || window.webContents.isLoading()) return false;
  const currentUrl = window.webContents.getURL();
  if (!currentUrl.startsWith("http://") && !currentUrl.startsWith("https://")) return false;
  try {
    return await window.webContents.executeJavaScript(
      'document.documentElement.dataset.camReady === "1"',
      true,
    ) === true;
  } catch {
    return false;
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
    autoHideMenuBar: true,
    backgroundColor: "#f5f7fb",
    title: "Creative Asset Manager",
    webPreferences: {
      preload: join(currentDirectory, "../preload/index.cjs"),
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      webviewTag: false,
      allowRunningInsecureContent: false,
    },
  });

  startupLog("window-created", { target: camUrl.origin });

  // Keep reliable native Windows controls, but remove Electron's application
  // menu so the shell reads as a focused desktop product.
  window.setMenuBarVisibility(false);
  window.removeMenu();

  const session = window.webContents.session;
  session.setPermissionRequestHandler((_contents, _permission, callback) => {
    callback(false);
  });
  session.setPermissionCheckHandler(() => false);

  let retryAttempt = 0;
  let recoveryTimer: ReturnType<typeof setTimeout> | undefined;
  let readinessGeneration = 0;
  let rendererReady = false;
  let unresponsiveTimer: ReturnType<typeof setTimeout> | undefined;

  const clearRecoveryTimer = () => {
    if (recoveryTimer) clearTimeout(recoveryTimer);
    recoveryTimer = undefined;
  };

  const showTerminalError = (detail?: string) => {
    clearRecoveryTimer();
    readinessGeneration += 1;
    rendererReady = false;
    startupLog("terminal-load-error", { detail: detail?.slice(0, 180) });
    void window.loadURL(shellDataUrl("error", camUrl, detail)).catch(() => undefined);
  };

  const scheduleRecovery = (reason: string, detail?: string) => {
    if (window.isDestroyed() || recoveryTimer) return;
    if (retryAttempt >= MAX_STARTUP_NAVIGATION_RETRIES) {
      showTerminalError(detail || "The workspace did not finish loading.");
      return;
    }
    const attempt = retryAttempt;
    retryAttempt += 1;
    const delayMs = startupRetryDelayMs(attempt);
    startupLog("recovery-scheduled", { reason, attempt: retryAttempt, delayMs });
    recoveryTimer = setTimeout(() => {
      recoveryTimer = undefined;
      rendererReady = false;
      readinessGeneration += 1;
      startupLog("recovery-start", { reason, attempt: retryAttempt });
      void loadApplication(true);
    }, delayMs);
    recoveryTimer.unref?.();
  };

  const watchRendererReadiness = () => {
    const generation = ++readinessGeneration;
    const startedAt = Date.now();
    const check = async () => {
      if (window.isDestroyed() || generation !== readinessGeneration) return;
      if (await isDesktopRendererReady(window)) {
        rendererReady = true;
        retryAttempt = 0;
        startupLog("renderer-ready", { readyMs: Date.now() - startedAt });
        return;
      }
      if (Date.now() - startedAt >= RENDERER_READY_TIMEOUT_MS) {
        scheduleRecovery("renderer-ready-timeout", "The interface did not finish starting.");
        return;
      }
      const timer = setTimeout(() => { void check(); }, 250);
      timer.unref?.();
    };
    void check();
  };

  const loadApplication = async (bypassCache: boolean) => {
    if (window.isDestroyed()) return;
    rendererReady = false;
    readinessGeneration += 1;
    startupLog("navigation-start", { bypassCache });
    try {
      if (bypassCache) {
        // A main-document refresh alone is not enough when Chromium retained an
        // old Vite entry/chunk. Clear only transient HTTP/code caches; cookies,
        // Local Storage and authenticated sessions stay intact.
        await Promise.allSettled([
          session.clearCache(),
          session.clearCodeCaches({ urls: [] }),
        ]);
        startupLog("recovery-cache-cleared");
      }
      await window.loadURL(
        camUrl.toString(),
        bypassCache
          ? { extraHeaders: "Cache-Control: no-cache\r\nPragma: no-cache\r\n" }
          : undefined,
      );
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      startupLog("navigation-rejected", { message: message.slice(0, 180) });
      scheduleRecovery("load-url-rejected", message);
    }
  };

  window.webContents.setWindowOpenHandler(({ url }) => {
    const disposition = classifyNavigation(url, camUrl);

    if (disposition === "internal") {
      retryAttempt = 0;
      void window.loadURL(url);
    } else {
      openExternalIfValid(url, camUrl);
    }

    return { action: "deny" };
  });

  const blockUnexpectedNavigation = (event: Electron.Event, url: string) => {
    if (url.startsWith("data:text/html")) return;
    const disposition = classifyNavigation(url, camUrl);

    if (disposition !== "internal") {
      event.preventDefault();
      openExternalIfValid(url, camUrl);
      return;
    }

    // A retry from the local recovery page gets a fresh retry budget.
    if (window.webContents.getURL().startsWith("data:text/html")) {
      retryAttempt = 0;
    }
  };

  window.webContents.on("will-navigate", blockUnexpectedNavigation);
  window.webContents.on("will-redirect", blockUnexpectedNavigation);

  window.webContents.on(
    "did-fail-load",
    (_event, errorCode, errorDescription, _validatedURL, isMainFrame) => {
      if (!isMainFrame || errorCode === -3) return;
      rendererReady = false;
      startupLog("did-fail-load", { errorCode, errorDescription });
      if (shouldRetryMainFrameLoad(isMainFrame, errorCode, retryAttempt)) {
        scheduleRecovery("did-fail-load", errorDescription);
      } else {
        showTerminalError(errorDescription);
      }
    },
  );

  window.webContents.on("did-finish-load", () => {
    const currentUrl = window.webContents.getURL();
    if (currentUrl.startsWith(camUrl.origin)) {
      startupLog("did-finish-load", { url: currentUrl.slice(0, 180) });
      watchRendererReadiness();
    }
  });

  window.webContents.on("render-process-gone", (_event, details) => {
    rendererReady = false;
    startupLog("render-process-gone", { reason: details.reason, exitCode: details.exitCode });
    if (!window.isDestroyed()) {
      retryAttempt = Math.min(retryAttempt, MAX_STARTUP_NAVIGATION_RETRIES - 1);
      scheduleRecovery("render-process-gone", `Renderer exited: ${details.reason}`);
    }
  });

  window.on("unresponsive", () => {
    startupLog("renderer-unresponsive");
    if (unresponsiveTimer) clearTimeout(unresponsiveTimer);
    unresponsiveTimer = setTimeout(() => {
      unresponsiveTimer = undefined;
      if (!rendererReady && !window.isDestroyed()) {
        scheduleRecovery("renderer-unresponsive", "The interface stopped responding during startup.");
      }
    }, UNRESPONSIVE_RECOVERY_DELAY_MS);
    unresponsiveTimer.unref?.();
  });

  window.on("responsive", () => {
    if (unresponsiveTimer) clearTimeout(unresponsiveTimer);
    unresponsiveTimer = undefined;
    startupLog("renderer-responsive");
  });

  window.on("closed", () => {
    clearRecoveryTimer();
    if (unresponsiveTimer) clearTimeout(unresponsiveTimer);
    readinessGeneration += 1;
  });

  // Render a tiny local shell first so users never stare at a blank window
  // while cache cleanup, DNS/TLS or the production SPA is starting.
  void window.loadURL(shellDataUrl("loading", camUrl)).then(async () => {
    if (window.isDestroyed()) return;
    window.show();
    startupLog("loading-shell-visible");

    const refreshedCaches = await prepareTransientCachesOnVersionChange({
      userDataPath: app.getPath("userData"),
      currentVersion: app.getVersion(),
      clearHttpCache: () => session.clearCache(),
      clearCodeCaches: () => session.clearCodeCaches({ urls: [camUrl.origin] }),
    }).catch(error => {
      startupLog("cache-refresh-failed", {
        message: error instanceof Error ? error.message.slice(0, 180) : String(error).slice(0, 180),
      });
      return false;
    });

    startupLog("cache-prepared", { refreshedCaches });
    await loadApplication(refreshedCaches);
  }).catch(error => {
    startupLog("loading-shell-failed", {
      message: error instanceof Error ? error.message.slice(0, 180) : String(error).slice(0, 180),
    });
    window.show();
    void loadApplication(true);
  });

  return window;
}
