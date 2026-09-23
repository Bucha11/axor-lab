import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The dev server proxies the screen API to a locally running `lab_server`, so
// the app is integrated against a REAL endpoint from the first screen. The
// plan's rule for Phase 4 is that a screen is "integrated" when it renders only
// endpoint output — a dev setup that serves fixtures makes that rule unfalsifiable.
const API = process.env.AXOR_LAB_API ?? "http://127.0.0.1:8871";
// The axor-identity login service is a SEPARATE process (see lib/identity.ts).
// The client calls it under `/identity` on its own origin; here, as in the
// deployed proxy, that prefix is stripped so the request reaches `/v1/*` on the
// identity service. Port 8402 is the control-plane repo's dev default.
const IDENTITY = process.env.AXOR_IDENTITY_API ?? "http://127.0.0.1:8402";

export default defineConfig({
  plugins: [react()],
  server: {
    // EVERY prefix the client calls. The list was written once and then went
    // stale as endpoints were added, and the failure is not a 404 you notice —
    // an unproxied path falls through to index.html, so `/auth/status` returned
    // HTML, the JSON parse threw, the probe's catch assumed auth was required,
    // and the dev app showed a LOGIN SCREEN against an open server. Which means
    // nobody could run the real UI against a real backend at all.
    //
    // `/guest-session` and `/incidents` were missing from it — the guest button
    // and incident import both "failed" in dev against a server that serves them.
    proxy: {
      ...Object.fromEntries(
        [
          "/artifacts", "/auth", "/billing", "/evidence", "/experiments",
          "/guest-session", "/handoff", "/home", "/hosted-runtimes", "/incidents",
          "/playground", "/publications", "/registry", "/regressions", "/runs",
          "/runtime", "/runtimes", "/scenarios", "/suites", "/verify", "/workspaces",
        ].map((path) => [path, { target: API, changeOrigin: true }]),
      ),
      "/identity": {
        target: IDENTITY,
        changeOrigin: true,
        rewrite: (path: string) => path.replace(/^\/identity/, ""),
      },
    },
  },
  test: {
    globals: true,
    environment: "node",
    // unit tests live in src/; e2e/ is Playwright (@playwright/test), a
    // different runner — keep vitest from collecting those specs
    include: ["src/**/*.{test,spec}.ts"],
  },
});
