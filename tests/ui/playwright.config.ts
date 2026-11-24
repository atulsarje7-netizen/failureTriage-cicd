import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

export default defineConfig({
  testDir: "./specs",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: "playwright-report" }],
    ["json", { outputFile: "test-results/results.json" }],
  ],
  use: {
    baseURL: "http://127.0.0.1:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: {
    command: `rm -f playwright.db && DATABASE_URL=sqlite:///playwright.db ${process.env.VIRTUAL_ENV ? process.env.VIRTUAL_ENV + "/bin/python" : "python3"} -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`,
    cwd: root,
    url: "http://127.0.0.1:8000/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
