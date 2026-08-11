import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "../tests/e2e",
  fullyParallel: false,
  retries: 0,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:8091",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } },
    },
  ],
  webServer: {
    command:
      "cd .. && APP_ENV=test STORE_BACKEND=local LOCAL_STORE_DIR=.local_store/e2e " +
      "DEFAULT_RUNTIME_MODE=snapshot uv run uvicorn backend.app.main:app " +
      "--host 127.0.0.1 --port 8091 --no-access-log",
    url: "http://127.0.0.1:8091/api/v1/health",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
