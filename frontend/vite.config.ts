import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Two backends in dev:
//   /api and /e  -> the publications/catalog server (python -m lab_server, :8000)
//   /jobs-api    -> the runtime-jobs server (--runtime-port 8010), prefix stripped
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/e": "http://127.0.0.1:8000",
      "/jobs-api": {
        target: "http://127.0.0.1:8010",
        rewrite: (path) => path.replace(/^\/jobs-api/, ""),
      },
    },
  },
});
