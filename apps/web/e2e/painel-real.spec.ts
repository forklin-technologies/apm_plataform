import { expect, test } from "@playwright/test";
import { CARLA, ELISA, apiAcceptsThisOrigin, demoPassword, loginAs } from "./auth-helpers";

/**
 * TASK-011 (fatia 2, passo B): painel com numeros REAIS da escola do vinculo ativo.
 * Precisa da API (com o seed financeiro) e do `npm run dev` na 3101 + a senha de demonstracao
 * (veja e2e/auth-helpers.ts). O teste de dinheiro em caixa cria um lancamento de demonstracao.
 */
test.skip(!demoPassword() || !apiAcceptsThisOrigin(), "precisa da senha de demonstracao e de E2E_BASE_URL=http://127.0.0.1:3101");

test("tesoureira: resumo com os dois saldos, pendencias fora do saldo e PDF do mes", async ({ page }) => {
  await loginAs(page, CARLA.email);
  await expect(page.locator("main").getByText(CARLA.school).first()).toBeVisible();
  await expect(page.getByText(/Saldo em caixa em/)).toBeVisible();
  await expect(page.getByText(/Após reembolsos pendentes:/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Pendências (fora do saldo)" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "A pagar" })).toBeVisible();
  await expect(page.locator("main")).not.toContainText(/Protótipo/);

  // o PDF do mes e uma navegacao com o cookie de sessao: 200 application/pdf
  const href = await page.getByRole("link", { name: "Baixar PDF das contribuições do mês" }).getAttribute("href");
  expect(href).toMatch(/^\/api\/v1\/schools\/[0-9a-f-]{36}\/reports\/contributions\.pdf\?period=\d{4}-\d{2}$/);
  const pdf = await page.request.get(href!);
  expect(pdf.status()).toBe(200);
  expect(pdf.headers()["content-type"]).toContain("application/pdf");
  expect((await pdf.body()).subarray(0, 4).toString()).toBe("%PDF");
});

test("tesoureira: lancamentos por tipo, mes anterior e dinheiro em caixa", async ({ page }) => {
  await loginAs(page, CARLA.email);
  // (navega por URL: no celular a barra de abas fica sob o botao do overlay do Next em desenvolvimento)
  await page.goto("/painel?tipo=contribuicoes");
  const list = page.getByRole("list", { name: "Contribuições" });
  await expect(list).toBeVisible();
  expect(await list.getByRole("listitem").count()).toBeGreaterThan(0);
  await expect(list.getByText("Pago").first()).toBeVisible();

  // seletor de mes: o mes passa para a URL e os numeros recarregam
  const select = page.getByLabel("Mês");
  const options = await select.locator("option").allTextContents();
  expect(options.length).toBeGreaterThanOrEqual(12);
  await select.selectOption({ index: 1 });
  await expect(page).toHaveURL(/mes=\d{4}-\d{2}/);

  // registrar contribuicao em dinheiro (so quem tem a permissao ve o botao)
  await page.goto("/painel");
  await page.getByRole("button", { name: "Registrar contribuição em dinheiro" }).click();
  await page.getByLabel("Valor").fill("1234");
  await page.getByLabel(/Nome do responsável/).fill("Pessoa E2E Dinheiro");
  await page.getByRole("button", { name: "Registrar", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: /registrada: R\$\s12,34/ })).toBeVisible();
});

test("viewer: so o resumo do mes, sem lancamentos e sem botao de dinheiro, e a tela nao quebra", async ({ page }) => {
  await loginAs(page, ELISA.email);
  await expect(page.getByText(/Saldo em caixa em/)).toBeVisible();
  await expect(page.getByText(/vê só os totais do mês/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Contribuições", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Registrar contribuição em dinheiro" })).toHaveCount(0);
  // mesmo digitando a URL, o perfil nao ve lancamentos
  await page.goto("/painel?tipo=contribuicoes");
  await expect(page.getByText(/não vê os lançamentos/)).toBeVisible();
});
