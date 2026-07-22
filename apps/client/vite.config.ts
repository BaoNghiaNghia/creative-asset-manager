import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", sourcemap: false },
  server: {
    // Development only. Production requests remain same-origin under /api.
    proxy: { "/api": "http://localhost:8000" },
  },
});
