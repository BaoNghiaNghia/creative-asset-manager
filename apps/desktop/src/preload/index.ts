import { contextBridge, ipcRenderer, webUtils } from "electron";
import type { Destination, IngestionJobView } from "../main/ingestion";

function acceptedPaths(files: FileList): string[] {
  const paths: string[] = [];
  for (const file of Array.from(files)) {
    try { const value = webUtils.getPathForFile(file); if (value) paths.push(value); } catch { /* non-OS-backed file */ }
  }
  return paths;
}
contextBridge.exposeInMainWorld("camDesktop", Object.freeze({
  isDesktop: true as const,
  platform: process.platform,
  beginOAuth: (request: { provider?: "google" | "microsoft"; intent?: "google_drive_connect" | "onedrive_connect"; externalSourceId?: string }) => ipcRenderer.invoke("desktop:oauth:begin", request),
  onAuthComplete: (callback: () => void) => { const listener = () => callback(); ipcRenderer.on("desktop-auth-complete", listener); return () => ipcRenderer.removeListener("desktop-auth-complete", listener); },
  ingestion: Object.freeze({
    acceptDrop: (files: FileList, destination: Destination) => ipcRenderer.invoke("desktop:ingestion:drop", acceptedPaths(files), destination),
    chooseFolders: (destination: Destination) => ipcRenderer.invoke("desktop:ingestion:choose-folders", destination),
    snapshot: (jobId: string) => ipcRenderer.invoke("desktop:ingestion:snapshot", jobId),
    pause: (jobId: string) => ipcRenderer.invoke("desktop:ingestion:pause", jobId),
    resume: (jobId: string) => ipcRenderer.invoke("desktop:ingestion:resume", jobId),
    cancel: (jobId: string) => ipcRenderer.invoke("desktop:ingestion:cancel", jobId),
    retry: (jobId: string, itemId: string) => ipcRenderer.invoke("desktop:ingestion:retry", jobId, itemId),
    onProgress: (callback: (job: IngestionJobView) => void) => { const listener = (_event: Electron.IpcRendererEvent, job: IngestionJobView) => callback(job); ipcRenderer.on("desktop:ingestion:progress", listener); return () => ipcRenderer.removeListener("desktop:ingestion:progress", listener); },
  }),
}));
