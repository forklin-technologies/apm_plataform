import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";
import { demoPassword, loginAs } from "./auth-helpers";

const INVITE = "/accept-invitation?token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG";
const routes = [
  "/",
  "/escola/demo-aurora",
  "/login",
  "/accept-invitation",
  INVITE,
  "/escola/nao-existe",
];

async function axe(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
  return results.violations.map((v) => `${v.id}: ${v.nodes[0]?.target.join(" ")}`);
}

for (const route of routes) {
  test(`axe sem violacoes em ${route}`, async ({ page }) => {
    await page.goto(route);
    await page.waitForTimeout(800);
    expect(await axe(page)).toEqual([]);
  });
}

// O painel exige sessao: precisa da API real e da senha de demonstracao (veja e2e/auth-helpers.ts).
test.describe("painel (logado)", () => {
  test.skip(!demoPassword(), "sem E2E_DEMO_PASSWORD_FILE/E2E_DEMO_PASSWORD");
  test("axe sem violacoes em /painel", async ({ page }) => {
    await loginAs(page);
    await page.waitForTimeout(800);
    expect(await axe(page)).toEqual([]);
  });
});
