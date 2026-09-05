import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld(
  "camDesktop",
  Object.freeze({
    isDesktop: true as const,
    platform: process.platform,
    beginOAuth: (request: { provider?: "google" | "microsoft"; intent?: "google_drive_connect" | "onedrive_connect"; externalSourceId?: string }) =>
      ipcRenderer.invoke("desktop:oauth:begin", request),
    onAuthComplete: (callback: () => void) => {
      const listener = () => callback();
      ipcRenderer.on("desktop-auth-complete", listener);
      return () => ipcRenderer.removeListener("desktop-auth-complete", listener);
    },
  }),
);
