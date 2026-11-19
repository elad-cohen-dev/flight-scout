import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// FS_API_URL points the dev proxy at another backend (e.g. a demo database on another port).
const api = process.env.FS_API_URL ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Backend search can take a minute+ — give the proxy a generous timeout.
      "/api": {
        target: api,
        changeOrigin: true,
        timeout: 300_000,
        proxyTimeout: 300_000,
      },
    },
  },
});
