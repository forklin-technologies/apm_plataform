import { defineConfig, devices } from "@playwright/test";

/**
 * Testes de navegador (WebKit e Chromium). Nao fazem parte do `npm test` (Vitest).
 *   npx playwright install webkit chromium     # uma vez
 *   npm run test:e2e                           # builda e sobe em 127.0.0.1:3110
 *   E2E_BASE_URL=http://127.0.0.1:3100 npm run test:e2e   # contra uma previa ja no ar
 */
const external = process.env.E2E_BASE_URL;
const baseURL = external ?? "http://127.0.0.1:3110";

const mobile = { viewport: { width: 390, height: 844 }, colorScheme: "dark" as const };
const desktop = { viewport: { width: 1440, height: 900 }, colorScheme: "light" as const };

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 4,
  reporter: [["list"]],
  use: { baseURL, trace: "off", locale: "pt-BR" },
  projects: [
    { name: "webkit-mobile", use: { ...devices["Desktop Safari"], ...mobile } },
    { name: "webkit-desktop", use: { ...devices["Desktop Safari"], ...desktop } },
    { name: "chromium-mobile", use: { ...devices["Desktop Chrome"], ...mobile } },
    { name: "chromium-desktop", use: { ...devices["Desktop Chrome"], ...desktop } },
  ],
  webServer: external
    ? undefined
    : {
        command: "npm run build && npm run start -- -p 3110 -H 127.0.0.1",
        url: `${baseURL}/login`,
        timeout: 300_000,
        reuseExistingServer: true,
      },
});
