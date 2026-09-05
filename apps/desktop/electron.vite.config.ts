import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig } from "electron-vite";

const rootDirectory = dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  main: {},
  preload: {},
  renderer: {
    build: {
      rollupOptions: {
        input: resolve(rootDirectory, "src/renderer/index.html"),
      },
    },
  },
});
