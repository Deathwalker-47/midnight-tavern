import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // Don't pick up Playwright spec files in this config.
    exclude: ["node_modules", "dist", "tests/e2e/**"],
    css: false,
  },
});
