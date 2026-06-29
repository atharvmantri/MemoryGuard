/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";

// Vitest config for the OSS TypeScript SDK. The SDK is a plain REST client
// with no DOM dependency, so tests run in the Node environment.
export default defineConfig({
  test: {
    environment: "node",
    globals: true,
    include: ["src/**/*.test.ts"],
  },
});
