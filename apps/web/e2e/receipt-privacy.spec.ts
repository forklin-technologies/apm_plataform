import { randomBytes } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

/**
 * N1 e N8: o comprovante so afirma o que a camada de dados confirma, e dado de crianca nao fica no
 * navegador nem na URL. Roda com a CSP real (sem bypassCSP).
 */
const token = () => randomBytes(16).toString("base64url"); // 22 caracteres, como o mock
const DEMO = "demo-comprovante-0001";

test("N1: o HTML cru (sem JavaScript) de um token qualquer nunca contem 'Pago'", async ({ request }) => {
  for (let i = 0; i < 5; i += 1) {
    const response = await request.get(`/apm/escola-exemplo/pedido/${token()}`);
    expect(response.status()).toBe(200);
    const html = await response.text();
    expect(html).toContain("Conferindo seu comprovante");
    expect(html).not.toMatch(/\bPago\b/);
    expect(html).not.toMatch(/Pagamento confirmado|Contribuição confirmada/);
    expect(html).not.toContain("Nome do responsável");
  }
});

test("N1: so o link de exemplo traz 'Pago' no HTML cru", async ({ request }) => {
  const html = await (await request.get(`/apm/escola-exemplo/pedido/${DEMO}`)).text();
  expect(html).toContain("Contribuição confirmada");
  expect(html).toMatch(/\bPago\b/);
});

test("N1: token plausivel que a camada de dados nao conhece vira a mesma 404 estilizada, sem 'Pago'", async ({ page }) => {
  await page.goto(`/apm/escola-exemplo/pedido/${token()}`);
  await expect(page.getByRole("heading", { name: "Não encontramos esta página da APM" })).toBeVisible();
  expect(await page.content()).not.toMatch(/\bPago\b/);
});

test("N1: token em formato invalido e 404 de verdade (HTTP) com a pagina estilizada", async ({ request, page }) => {
  for (const bad of ["teste1", "a".repeat(21), "..%2Fetc%2Fpasswd"]) {
    expect((await request.get(`/apm/escola-exemplo/pedido/${bad}`)).status()).toBe(404);
  }
  await page.goto("/apm/escola-exemplo/pedido/teste1");
  await expect(page.getByRole("heading", { name: "Não encontramos esta página da APM" })).toBeVisible();
});

async function payAndOpenReceipt(page: Page) {
  await page.goto("/apm/escola-exemplo");
  await page.getByText("Cota anual").first().click();
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByLabel("Nome do responsável").fill("Ana Paula Lima");
  await page.getByLabel("Nome do aluno").fill("Davi Lima");
  await page.getByLabel(/Turma/).fill("4º B");
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: /Gerar Pix/ }).click();
  await expect(page.getByRole("heading", { name: "Pagamento confirmado" })).toBeVisible({ timeout: 40_000 });
  await page.getByRole("link", { name: "Ver comprovante" }).click();
  await expect(page.getByRole("heading", { name: "Contribuição confirmada" })).toBeVisible();
}

test("N8: dados pessoais saem do sessionStorage depois de mostrar o comprovante; o link segue abrindo sem eles", async ({ page }) => {
  await page.goto("/apm/escola-exemplo");
  await page.getByText("Cota anual").first().click();
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByLabel("Nome do responsável").fill("Ana Paula Lima");
  await page.getByLabel("Nome do aluno").fill("Davi Lima");
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: /Gerar Pix/ }).click();
  await expect(page.getByRole("heading", { name: "Pague com Pix" })).toBeVisible();
  // enquanto a contribuicao nao termina, o mock precisa guardar os dados para montar o comprovante
  expect(await page.evaluate(() => sessionStorage.getItem("apm-proto:orders"))).toContain("Ana Paula Lima");

  await expect(page.getByRole("heading", { name: "Pagamento confirmado" })).toBeVisible({ timeout: 40_000 });
  await page.getByRole("link", { name: "Ver comprovante" }).click();
  await expect(page.getByRole("heading", { name: "Contribuição confirmada" })).toBeVisible();
  await expect(page.getByText("Ana Paula Lima")).toBeVisible(); // mostrado uma vez

  await expect.poll(() => page.evaluate(() => sessionStorage.getItem("apm-proto:orders") ?? "")).not.toContain("Ana Paula Lima");
  expect(await page.evaluate(() => JSON.stringify({ ...sessionStorage, ...localStorage }))).not.toMatch(/Ana Paula|Davi Lima/);

  await page.reload();
  await expect(page.getByRole("heading", { name: "Contribuição confirmada" })).toBeVisible();
  await expect(page.getByText("Ana Paula Lima")).toHaveCount(0);
  await expect(page.getByText("Davi Lima")).toHaveCount(0);
});

test("N8: nenhum dado digitado aparece na URL durante o fluxo", async ({ page }) => {
  const urls: string[] = [];
  page.on("framenavigated", (frame) => urls.push(frame.url()));
  await payAndOpenReceipt(page);
  for (const url of urls) {
    expect(url, url).not.toMatch(/Ana|Davi|Paula|guardianName|studentName|classroom|custom-amount|amount-choice/);
    expect(new URL(url).search, url).toBe("");
  }
});

test.describe("N8: sem JavaScript (antes da hidratacao) o formulario nao vaza dados em GET", () => {
  test.use({ javaScriptEnabled: false });

  test("o Enter/Continuar do passo do valor faz POST e a URL continua sem query", async ({ page }) => {
    const methods: string[] = [];
    page.on("request", (r) => {
      if (r.isNavigationRequest() && r.url().includes("/apm/escola-exemplo")) methods.push(`${r.method()} ${new URL(r.url()).search}`);
    });
    await page.goto("/apm/escola-exemplo");
    await page.getByText("Cota mensal", { exact: true }).click(); // marca o radio nativamente
    await page.getByRole("button", { name: "Continuar" }).click();
    await page.waitForLoadState("domcontentloaded");
    expect(methods.some((m) => m.startsWith("POST"))).toBe(true);
    expect(methods.filter((m) => m.startsWith("GET")).every((m) => m.endsWith(" "))).toBe(true); // GET so da carga inicial, sem query
    expect(new URL(page.url()).search).toBe("");
  });
});
