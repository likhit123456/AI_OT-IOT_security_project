import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  base: "/",
  build: {
    outDir: path.resolve(__dirname, "../live/static"),
    emptyOutDir: false,
    assetsDir: "assets",
    chunkSizeWarningLimit: 600
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
      "/grfics": "http://127.0.0.1:8765",
      "/Build": "http://127.0.0.1:8765",
      "/TemplateData": "http://127.0.0.1:8765",
      "/data": "http://127.0.0.1:8765"
    }
  }
});
