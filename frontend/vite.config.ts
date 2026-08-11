/// <reference types="vitest/config" />

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "../backend/app/static",
    emptyOutDir: true,
  },
  server: {
    proxy: { "/api": "http://127.0.0.1:8080" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test-setup.ts",
  },
});
