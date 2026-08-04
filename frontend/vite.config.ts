import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA talks to the FastAPI backend under /api/v1; in dev we proxy /api to
// the backend on :8000 so no CORS config is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Quick-tunnel demo access (docs/remote-access-tunnel.md): Cloudflare
    // assigns a random *.trycloudflare.com hostname per run; allow that
    // suffix so the public URL can reach the dev server. Dev-only setting.
    allowedHosts: [".trycloudflare.com"],
    proxy: {
      // Push channel (design 22): WS upgrade must go to the backend too.
      "/api/v1/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
