import { expect, test } from "@playwright/test";
import { apiAcceptsThisOrigin } from "./auth-helpers";

/**
 * TASK-011 (fatia 2, passo A): portal publico contra a API REAL, no sandbox de desenvolvimento.
 * Precisa da API no ar (docker compose) e do `npm run dev` na 3101: E2E_BASE_URL=http://127.0.0.1:3101.
 * Cria contribuicoes de demonstracao na escola demo-aurora (dados falsos, banco local).
 */
test("/apm/{slug} redireciona para /escola/{slug}", async ({ page }) => {
  await page.goto("/apm/demo-aurora");
  await expect(page).toHaveURL(/\/escola\/demo-aurora$/);
});

// A API conta toda consulta que erra (token ou slug inexistente) contra o endereco e bloqueia (429) depois
// de algumas: por isso so UMA consulta ruim chega a ela aqui. O formato invalido ("curto") nem sai do site.
test("token ruim no comprovante: 404 de verdade", async ({ request }, testInfo) => {
  test.skip(!apiAcceptsThisOrigin(), "precisa da API real (E2E_BASE_URL=http://127.0.0.1:3101)");
  test.skip(testInfo.project.name !== "chromium-desktop", "uma consulta ruim so, para nao bloquear os outros fluxos do mesmo endereco");
  expect((await request.get("/escola/demo-aurora/pedido/curto")).status()).toBe(404);
  expect((await request.get("/escola/demo-aurora/pedido/" + "x".repeat(43))).status()).toBe(404);
});

test("valor livre abaixo do minimo da escola: erro na tela, sem chamar a API", async ({ page }) => {
  test.skip(!apiAcceptsThisOrigin(), "precisa da API real (E2E_BASE_URL=http://127.0.0.1:3101)");
  const posts: string[] = [];
  page.on("request", (r) => r.method() === "POST" && posts.push(r.url()));
  await page.goto("/escola/demo-aurora");
  await page.getByText("Outro valor", { exact: true }).click();
  await page.getByLabel("Valor da contribuição").fill("500");
  await page.getByRole("button", { name: "Continuar" }).click();
  await expect(page.getByText(/O valor mínimo é/)).toBeVisible();
  expect(posts).toEqual([]);
});

test("fluxo completo: valor, dados, QR, simular pagamento, comprovante", async ({ page }) => {
  test.skip(!apiAcceptsThisOrigin(), "precisa da API real (E2E_BASE_URL=http://127.0.0.1:3101)");
  const creates: Array<{ url: string; key: string | undefined }> = [];
  page.on("request", (r) => {
    if (r.method() === "POST" && r.url().endsWith("/contributions")) creates.push({ url: r.url(), key: r.headers()["idempotency-key"] });
  });

  await page.goto("/escola/demo-aurora");
  await expect(page.getByRole("heading", { name: "Quanto você quer contribuir?" })).toBeVisible();
  await page.getByRole("radio", { name: /20,00/ }).check({ force: true });
  await page.getByRole("button", { name: "Continuar" }).click();

  // so os campos que a escola pede (aluno e turma estao ocultos)
  await expect(page.getByLabel(/Nome do responsável/)).toBeVisible();
  await expect(page.getByLabel(/Nome do aluno/)).toHaveCount(0);
  await page.getByLabel(/Nome do responsável/).fill("Maria E2E");
  await page.getByRole("button", { name: "Continuar" }).click();

  await page.getByRole("button", { name: /Gerar Pix de/ }).click();
  await expect(page.getByRole("heading", { name: "Pague com Pix" })).toBeVisible();
  await expect(page.getByRole("img", { name: /QR Code do Pix/ })).toBeVisible();
  const payload = await page.getByLabel("Pix copia e cola").inputValue();
  expect(payload).toMatch(/^PIX-SANDBOX:[0-9a-f]+:2000$/);
  await expect(page.getByText("Pagamento confirmado")).toHaveCount(0);

  // a chave de idempotencia foi no cabecalho (uuid), nunca na URL
  expect(creates).toHaveLength(1);
  expect(creates[0]!.key).toMatch(/^[0-9a-f-]{36}$/);
  expect(creates[0]!.url).not.toContain(creates[0]!.key!);

  // sem simular, continua aguardando (nao afirma pagamento sozinho)
  await page.waitForTimeout(2500);
  await expect(page.getByText("Pagamento confirmado")).toHaveCount(0);

  await page.getByRole("button", { name: "Simular pagamento (somente desenvolvimento)" }).click();
  await expect(page.getByRole("heading", { name: "Pagamento confirmado" })).toBeVisible({ timeout: 20_000 });

  await page.getByRole("link", { name: "Ver comprovante" }).click();
  await expect(page).toHaveURL(/\/escola\/demo-aurora\/pedido\/[A-Za-z0-9_-]{43}$/);
  await expect(page.getByRole("heading", { name: "Contribuição confirmada" })).toBeVisible();
  await expect(page.locator("main")).toContainText(/APM-\d{6}/);
  await expect(page.locator("main")).toContainText("Pix");

  // o token so vive na URL: nada em storage
  const token = new URL(page.url()).pathname.split("/").pop()!;
  const stored = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  expect(stored).not.toContain(token);
  expect(stored).not.toContain(creates[0]!.key!);

  // recarregar: o servidor entrega o comprovante com 200 da API
  await page.reload();
  await expect(page.getByRole("heading", { name: "Contribuição confirmada" })).toBeVisible();
});
