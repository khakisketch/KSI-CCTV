import { defineConfig, devices } from "@playwright/test";

// Dedicated ports and no reuse prevent an unrelated local Next app from becoming test input.
const frontendPort = 4175;
const enginePort = 4176;
const mediaPort = 4177;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  // Login rate-limit state is intentionally process-global; serialize browser specs for deterministic evidence.
  workers: 1,
  retries: process.env.CI ? 2 : 0,
  reporter: process.env.CI
    ? [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]]
    : "list",
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: "node e2e/mock-engine.mjs",
      env: { ...process.env, MOCK_ENGINE_PORT: String(enginePort) },
      url: `http://127.0.0.1:${enginePort}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: "node e2e/mock-mediamtx.mjs",
      env: { ...process.env, MOCK_MEDIAMTX_PORT: String(mediaPort) },
      url: `http://127.0.0.1:${mediaPort}/health`,
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command: `npm run start -- --hostname 127.0.0.1 --port ${frontendPort}`,
      env: {
        ...process.env,
        KSI_CCTV_ENGINE_URL: `http://127.0.0.1:${enginePort}`,
        KSI_CCTV_GATEWAY_URL: `http://127.0.0.1:${mediaPort}`,
        KSI_CCTV_API_KEY: "e2e-engine-key",
        KSI_CCTV_WEB_PASSWORD: "e2e-web-password",
        KSI_CCTV_WEB_SECRET: "e".repeat(64),
        KSI_CCTV_WEB_COOKIE_SECURE: "false",
      },
      url: `http://127.0.0.1:${frontendPort}/login`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
