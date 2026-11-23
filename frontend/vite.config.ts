/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The page calls /api/... on its own origin; in development Vite passes those calls on
// to job-web. So the browser only ever talks to one origin, and server-sent events work
// without any cross-origin set-up.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    // Typing a whole advert key by key (user-event) can pass the default 5s on a busy laptop.
    testTimeout: 15_000,
  },
});
