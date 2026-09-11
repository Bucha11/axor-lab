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
    proxy: Object.fromEntries(
      [
        "/home", "/suites", "/runs", "/runtimes", "/runtime",
        "/evidence", "/regressions", "/artifacts", "/playground",
        "/scenarios", "/experiments",
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
