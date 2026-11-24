import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const externalBase = process.env.PLAYWRIGHT_BASE_URL;
const databaseUrl = process.env.DATABASE_URL ?? "sqlite:///playwright.db";
const pythonBin =
  process.env.PYTHON ??
  (process.env.VIRTUAL_ENV ? `${process.env.VIRTUAL_ENV}/bin/python` : "python3");

/** Quote a value for the webServer shell command. */
function shellQuote(value: string): string {
  return `'${value.replaceAll("'", "'\\''")}'`;
}

/** Include a shell assignment only when the value is non-empty. */
function forward(name: string, value: string | undefined): string | null {
  if (!value) {
    return null;
  }
  return `${name}=${shellQuote(value)}`;
}

const forwarded = [
  `DATABASE_URL=${shellQuote(databaseUrl)}`,
  forward("PAYMENT_URL", process.env.PAYMENT_URL),
  forward("PAYMENT_API_KEY", process.env.PAYMENT_API_KEY),
  forward("PAYMENT_TIMEOUT_S", process.env.PAYMENT_TIMEOUT_S),
  forward("APP_SEED_PASSWORD", process.env.APP_SEED_PASSWORD),
  `APP_LOG_PATH=${shellQuote(process.env.APP_LOG_PATH ?? "logs/app.log")}`,
  forward("FLAKY_SEED", process.env.FLAKY_SEED),
]
  .filter((part) => part !== null)
  .join(" ");

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
    baseURL: externalBase ?? "http://127.0.0.1:8000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
  webServer: externalBase
    ? undefined
    : {
        command: `rm -f playwright.db && ${forwarded} ${shellQuote(pythonBin)} -m uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000`,
        cwd: root,
        url: "http://127.0.0.1:8000/health",
        reuseExistingServer: false,
        timeout: Number(process.env.PLAYWRIGHT_WEBSERVER_TIMEOUT ?? 120_000),
      },
});
