import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test.use({ bypassCSP: true });

const routes = ["/", "/apm/escola-exemplo", "/apm/escola-exemplo/pedido/demo-comprovante-0001", "/painel", "/login", "/apm/nao-existe"];

for (const route of routes) {
  test(`axe sem violacoes em ${route}`, async ({ page }) => {
    await page.goto(route);
    await page.waitForTimeout(800);
    const results = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa", "best-practice"]).analyze();
    expect(results.violations.map((v) => `${v.id}: ${v.nodes[0]?.target.join(" ")}`)).toEqual([]);
  });
}
