import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Frontend unit/component tests (Vitest + jsdom + Testing Library).
// Conventions (see src/test/setup.ts for the shared mock strategy):
// - tests live next to their subject: src/<area>/__tests__/<name>.test.ts(x)
// - import describe/it/expect/vi/... explicitly from "vitest" (no globals)
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
    coverage: {
      provider: "v8",
      include: ["src/**"],
      exclude: ["**/*.test.*", "src/test/**", "src/main.tsx", "src/vite-env.d.ts"],
    },
  },
});
