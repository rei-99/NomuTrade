import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA talks to the FastAPI backend under /api/v1; in dev we proxy /api to
// the backend on :8000 so no CORS config is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
