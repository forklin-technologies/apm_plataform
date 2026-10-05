import { expect, test } from "@playwright/test";
import { ANA, apiAcceptsThisOrigin, demoPassword, loginAs } from "./auth-helpers";

/**
 * TASK-011 (fatia 1): login, sessao no painel e logout contra a API REAL.
 * Veja e2e/auth-helpers.ts para rodar (precisa da API no ar e do `npm run dev` na 3101).
 */
test("sem sessao, /painel volta para /login", async ({ page }) => {
  await page.goto("/painel");
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Entrar na tesouraria" })).toBeVisible();
});

test("senha errada: texto em portugues, igual para e-mail que nao existe, e a senha some do campo", async ({ page }) => {
  test.skip(!apiAcceptsThisOrigin(), "a API real so aceita POST de :3100/:3101 (E2E_BASE_URL)");
  await page.goto("/login");
  // e-mail unico por execucao: nao gasta o contador de tentativas de ninguem
  await page.getByLabel("E-mail").fill(`e2e-${Date.now()}-${Math.random().toString(36).slice(2, 8)}@example.test`);
  await page.getByLabel("Senha").fill("senha-que-nao-existe-123");
  await page.getByRole("button", { name: "Entrar" }).click();
  const alert = page.locator("main").getByRole("alert");
  await expect(alert).toContainText(/E-mail ou senha incorretos|Muitas tentativas/);
  await expect(alert).not.toContainText(/not exist|Invalid|invalid_credentials|não existe/i);
  await expect(page.getByLabel("Senha")).toHaveValue("");
  await expect(page).toHaveURL(/\/login$/);
});

test.describe("com a senha de demonstracao", () => {
  test.skip(!demoPassword() || !apiAcceptsThisOrigin(), "sem a senha de demonstracao ou fora de :3100/:3101");

  test("login real, painel com nome e vinculo reais, sair e /painel volta para /login", async ({ page }) => {
    await loginAs(page);

    // nome e vinculo REAIS (vindos de GET /auth/me)
    await expect(page.getByText(ANA.name).locator("visible=true").first()).toBeVisible();
    await expect(page.getByText(ANA.organization).locator("visible=true").first()).toBeVisible();
    await expect(page.getByRole("heading", { name: "Resumo" })).toBeVisible();

    // os dados financeiros continuam simulados e marcados
    await expect(page.getByRole("note").filter({ hasText: "Protótipo" })).toBeVisible();

    // a pagina nunca leu o cookie de sessao (HttpOnly): so o CSRF e legivel
    const names = (await page.context().cookies()).map((c) => c.name);
    expect(names).toContain("apm_session");
    const readable = await page.evaluate(() => document.cookie);
    expect(readable).toContain("apm_csrf");
    expect(readable).not.toContain("apm_session");

    // o seletor mostra os vinculos reais
    await page.getByRole("button", { name: new RegExp(ANA.organization) }).first().click();
    await expect(page.getByRole("group", { name: ANA.organization })).toBeVisible();
    await page.keyboard.press("Escape");

    await page.getByRole("button", { name: "Sair" }).first().click();
    await expect(page).toHaveURL(/\/login$/);
    await page.goto("/painel");
    await expect(page).toHaveURL(/\/login$/);
  });

  test("depois do login, ?next= so aceita destinos conhecidos", async ({ page }) => {
    await page.goto("/login?next=https://evil.example/");
    await page.getByLabel("E-mail").fill(ANA.email);
    await page.getByLabel("Senha").fill(demoPassword()!);
    await page.getByRole("button", { name: "Entrar" }).click();
    await expect(page).toHaveURL(/127\.0\.0\.1:\d+\/painel$/);
  });
});

test("convite sem token: aviso claro e nenhum formulario", async ({ page }) => {
  await page.goto("/accept-invitation");
  await expect(page.locator("main").getByRole("alert")).toContainText("incompleto ou inválido");
  await expect(page.getByRole("button", { name: /Criar conta/ })).toHaveCount(0);
});

test("convite com token invalido: erro por code, sem dizer o motivo exato", async ({ page }) => {
  test.skip(!apiAcceptsThisOrigin(), "a API real so aceita POST de :3100/:3101 (E2E_BASE_URL)");
  await page.goto("/accept-invitation?token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG");
  await page.getByLabel("Nome completo").fill("Pessoa de Teste");
  await page.getByLabel("Senha", { exact: true }).fill("uma-senha-bem-longa-123");
  await page.getByLabel("Repita a senha").fill("uma-senha-bem-longa-123");
  await page.getByRole("button", { name: "Criar conta e aceitar" }).click();
  await expect(page.locator("main").getByRole("alert")).toContainText(/Este convite não é válido|Muitas tentativas/);
});
