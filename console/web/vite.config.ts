/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The console API serves the built assets; `npm run dev` proxies to a running server.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", emptyOutDir: true, sourcemap: false, chunkSizeWarningLimit: 900 },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
