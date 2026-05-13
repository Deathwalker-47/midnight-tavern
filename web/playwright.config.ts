import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright e2e configuration. Runs against a real backend + Vite dev server.
 *
 * Two services must be reachable:
 *   - Vite dev server on http://localhost:5173 (frontend)
 *   - FastAPI backend on http://localhost:8000 (proxied through Vite)
 *
 * The webServer block boots Vite; the backend is expected to be running
 * separately (the CI workflow starts it before invoking Playwright).
 */
export default defineConfig({
  testDir: "./tests/e2e",
  // Don't pick up Vitest unit tests.
  testIgnore: ["**/*.test.ts", "**/*.test.tsx"],
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false, // single worker — shared backend DB state
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:5173",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 60_000,
  },
});
