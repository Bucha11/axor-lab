import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the screen API to a locally running `lab_server`, so
// the app is integrated against a REAL endpoint from the first screen. The
// plan's rule for Phase 4 is that a screen is "integrated" when it renders only
// endpoint output — a dev setup that serves fixtures makes that rule unfalsifiable.
const API = process.env.AXOR_LAB_API ?? "http://127.0.0.1:8871";

export default defineConfig({
  plugins: [react()],
  server: {
    // EVERY prefix the client calls. The list was written once and then went
    // stale as endpoints were added, and the failure is not a 404 you notice —
    // an unproxied path falls through to index.html, so `/auth/status` returned
    // HTML, the JSON parse threw, the probe's catch assumed auth was required,
    // and the dev app showed a LOGIN SCREEN against an open server. Which means
    // nobody could run the real UI against a real backend at all.
    proxy: Object.fromEntries(
      [
        "/artifacts", "/auth", "/billing", "/evidence", "/experiments", "/handoff",
        "/home", "/hosted-runtimes", "/playground", "/publications",
        "/registry", "/regressions", "/runs", "/runtime", "/runtimes",
        "/scenarios", "/suites", "/verify", "/workspaces",
      ].map((path) => [path, { target: API, changeOrigin: true }]),
    ),
  },
  test: {
    globals: true,
    environment: "node",
    // unit tests live in src/; e2e/ is Playwright (@playwright/test), a
    // different runner — keep vitest from collecting those specs
    include: ["src/**/*.{test,spec}.ts"],
  },
});
