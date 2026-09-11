import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

// The image pre-installs a fixed Chromium under PLAYWRIGHT_BROWSERS_PATH whose
// revision may not match the one this @playwright/test expects, so point
// straight at it when present (override with AXOR_CHROMIUM). On a plain runner
// it is absent — leave executablePath unset and use the managed browser.
const PREINSTALLED = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";
const chromiumPath =
  process.env.AXOR_CHROMIUM || (existsSync(PREINSTALLED) ? PREINSTALLED : undefined);

// End-to-end UI tests for the Lab web app. They drive the real built React app
// in a browser; the backend is stubbed per test with page.route, so the auth /
// guest / open-mode flows are exercised deterministically without a live
// lab_server. The Vite dev server is booted by webServer below.
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: chromiumPath ? { executablePath: chromiumPath } : {},
      },
    },
  ],
  webServer: {
    // bind explicitly to 127.0.0.1: on a CI runner Vite otherwise listens on
    // localhost (IPv6 ::1) while the readiness probe hits 127.0.0.1 (IPv4), and
    // the mismatch times the webServer out.
    command: "npm run dev -- --port 5173 --strictPort --host 127.0.0.1",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
