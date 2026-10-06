import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { CARLA, ELISA, apiAcceptsThisOrigin, apiSession, demoPassword, loginAs } from "./auth-helpers";

/**
 * TASK-011 (fatia 3): fechamento mensal contra a API REAL. A tesoureira fecha um mes, ve os dois saldos,
 * verifica e baixa o PDF (%PDF); o administrador da organizacao (months:reopen) desfaz pela API; o fechamento
 * reaberto nao tem PDF. Mes usado: JANEIRO DE 2026 (o demo so tem dados a partir de setembro, entao o
 * fechamento e vazio e nenhum outro teste precisa dele). Se ja existir um fechamento ativo na escola, o teste
 * NAO fecha nada e so confere o PDF desse; so desfaz o que ele mesmo fechou. Cada execucao que fecha deixa um
 * fechamento reaberto no historico.
 */
test.describe.configure({ mode: "serial" });
test.skip(!demoPassword() || !apiAcceptsThisOrigin(), "precisa da senha de demonstracao e de E2E_BASE_URL=http://127.0.0.1:3101");
test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "fecha mes: roda uma vez, em chromium-desktop");
});

const JAN = "janeiro de 2026";
test("tesoureira: fecha o mes, ve os dois saldos, verifica, baixa o PDF e a ordem dos meses e respeitada", async ({ page }) => {
  await loginAs(page, CARLA.email);
  await page.goto("/painel/fechamento");
  await expect(page.getByRole("heading", { level: 1, name: "Fechamento" })).toBeVisible();
  // tesoureira: fecha mes, mas nao reabre
  await expect(page.getByRole("heading", { name: "Fechar mês" })).toBeVisible();

  // espera a lista chegar: so entao se sabe se ja ha fechamento ativo (e o formulario ja esta no mes sugerido)
  await expect(page.getByRole("list", { name: "Fechamentos mensais" }).or(page.getByRole("heading", { name: "Nenhum mês fechado ainda" }))).toBeVisible();
  const hasActive = await page.locator('li[data-status="closed"]').count();
  if (hasActive === 0) {
    await page.getByLabel("Mês a fechar").selectOption("2026-01");
    await page.getByLabel(/Saldo informado pelo banco/).fill("0");
    await page.getByRole("button", { name: "Fechar mês", exact: true }).click();
    await expect(page.getByText(`Mês fechado: ${JAN}.`)).toBeVisible();
  }

  const active = page.locator('li[data-status="closed"]').first();
  // o fechamento recem-criado ja aparece aberto; um que ja existia precisa ser aberto
  const toggle = active.getByRole("button", { name: /o fechamento de/ });
  if ((await toggle.getAttribute("aria-expanded")) !== "true") await toggle.click();
  const detail = page.getByTestId("closing-detail");
  await expect(detail).toContainText("Saldo em caixa no fechamento");
  await expect(detail).toContainText("Após reembolsos pendentes");
  await expect(detail).toContainText("Código de verificação");
  await expect(page.getByRole("button", { name: "Reabrir mês" })).toHaveCount(0);

  await page.getByRole("button", { name: "Verificar", exact: true }).click();
  await expect(page.getByText("Verificado: os lançamentos do mês batem com este fechamento.")).toBeVisible();

  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Baixar PDF do extrato mensal" }).click()]);
  expect(download.suggestedFilename()).toMatch(/^extrato-mensal-\d{4}-\d{2}\.pdf$/);
  const bytes = readFileSync((await download.path())!);
  expect(bytes.subarray(0, 4).toString()).toBe("%PDF");

  // meses fecham em ordem: pular para um mes distante mostra o proximo mes que a API nomeou
  await page.getByLabel("Mês a fechar").selectOption("2026-08");
  await page.getByRole("button", { name: "Fechar mês", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: /Os meses são fechados em ordem\. O próximo mês a fechar é .+ de \d{4}\./ })).toBeVisible();
});

test("administrador da organizacao reabre pela API: o fechamento reaberto aparece sem PDF e sem verificar", async ({ page, playwright, baseURL }) => {
  const ana = await apiSession(playwright, baseURL!, "ana.admin@example.test");
  const carla = await apiSession(playwright, baseURL!, CARLA.email);
  const listed = await carla.context.get(`/api/v1/schools/${carla.schoolId}/closings?include_reopened=true`);
  const items = ((await listed.json()) as { items: Array<{ id: string; period: string; reopened_at: string | null }> }).items;
  const target = items.find((c) => c.period === "2026-01" && c.reopened_at === null);
  // so desfaz o que e de janeiro de 2026 (o mes deste teste); um fechamento de outro mes nao e tocado
  if (target) {
    const reopen = await ana.context.post(`/api/v1/schools/${carla.schoolId}/closings/${target.id}/reopen`, {
      headers: { "X-CSRF-Token": ana.csrf },
      data: { reason: "Fechamento de teste desfeito pelo e2e do site" },
    });
    expect(reopen.status()).toBe(200);
  }
  await ana.context.dispose();
  await carla.context.dispose();

  await loginAs(page, CARLA.email);
  await page.goto("/painel/fechamento");
  const reopened = page.locator('li[data-status="reopened"]').filter({ hasText: JAN }).first();
  await expect(reopened).toBeVisible();
  await reopened.getByRole("button", { name: /Abrir o fechamento/ }).click();
  await expect(page.getByRole("note").filter({ hasText: "não tem PDF nem verificação" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Baixar PDF do extrato mensal" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Verificar", exact: true })).toHaveCount(0);
});

test("viewer: so le o fechamento (sem fechar, sem reabrir) e nao ve despesas", async ({ page }) => {
  await loginAs(page, ELISA.email);
  await page.goto("/painel/fechamento");
  await expect(page.getByRole("heading", { level: 1, name: "Fechamento" })).toBeVisible();
  await expect(page.getByRole("note")).toContainText("Fechar o mês é da tesouraria e da direção");
  await expect(page.getByRole("button", { name: "Fechar mês" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reabrir mês" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Despesas" })).toHaveCount(0);
});
