import { defineConfig } from "@playwright/test";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "../..");
const python =
  process.env.PIPI_TEST_PYTHON ||
  path.join(
    root,
    ".venv",
    process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
  );
export default defineConfig({
  testDir: "./tests",
  timeout: 90000,
  expect: { timeout: 30000 },
  workers: 1,
  use: {
    baseURL: "http://localhost:13100",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
  },
  outputDir: "../../output/playwright/results",
  webServer: [
    {
      command: `"${python}" -m pipi.tests.e2e_server`,
      cwd: root,
      url: "http://127.0.0.1:18180/api/health",
      reuseExistingServer: !process.env.CI,
    },
    {
      command: "bun run dev -- --port 13100",
      url: "http://localhost:13100",
      env: {
        PIPI_BACKEND_URL: "http://127.0.0.1:18180",
        NEXT_TELEMETRY_DISABLED: "1",
      },
      reuseExistingServer: !process.env.CI,
    },
  ],
});
