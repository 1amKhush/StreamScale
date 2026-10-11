import { defineConfig } from "@playwright/test";

// Browser acceptance runs against a live console server (see e2e/session.ts).
// It uses the locally installed Google Chrome; no browser download is required.
export default defineConfig({
  testDir: "e2e",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  workers: 1,
  reporter: [["list"]],
  outputDir: "../../artifacts/console/e2e-results",
  use: {
    channel: process.env.CONSOLE_BROWSER_CHANNEL ?? "chrome",
    viewport: { width: 1440, height: 920 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
