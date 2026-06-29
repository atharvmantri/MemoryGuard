/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
// Vite config for the OSS local dashboard (Vault Mesh).
// Dev server binds to localhost only, consistent with the local-first design.
export default defineConfig({
    plugins: [react()],
    server: {
        host: "127.0.0.1",
        port: 5173,
    },
    build: {
        outDir: "dist",
        sourcemap: true,
    },
    test: {
        environment: "jsdom",
        globals: true,
        setupFiles: ["./src/test/setup.ts"],
    },
});
