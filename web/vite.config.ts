import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // Relative asset URLs so the built app also works when the Python server
  // hands it out from `python3 -m server --dist web/dist`.
  base: "./",
  server: {
    port: 5173,
    // Same-origin in dev, so there is no CORS to configure.
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: true } },
  },
  build: { outDir: "dist", sourcemap: true },
});
