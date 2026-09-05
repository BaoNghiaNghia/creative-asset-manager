import { contextBridge } from "electron";

contextBridge.exposeInMainWorld(
  "camDesktop",
  Object.freeze({
    isDesktop: true as const,
    platform: process.platform,
  }),
);
