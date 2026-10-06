import { expect, test } from "@playwright/test";
import { CARLA, apiAcceptsThisOrigin, demoPassword, loginAs } from "./auth-helpers";

/**
 * Alterar senha contra a API REAL. NAO troca a senha de ninguem (a senha de demonstracao e compartilhada):
 * so confere a validacao do cliente e o erro da API para a senha atual errada. A troca de verdade, com a
 * resposta 204, esta nos testes de unidade (PasswordForm). Uma tentativa errada por execucao (a API limita).
 */
test.skip(!demoPassword() || !apiAcceptsThisOrigin(), "precisa da senha de demonstracao e de E2E_BASE_URL=http://127.0.0.1:3101");

test("alterar senha: valida no cliente e mostra o erro da API para a senha atual incorreta, sem trocar nada", async ({ page }, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "uma tentativa errada so (a API limita as tentativas)");
  await loginAs(page, CARLA.email);
  await page.getByRole("link", { name: "Alterar senha" }).first().click();
  await expect(page).toHaveURL(/\/painel\/conta$/);
  await expect(page.getByRole("heading", { level: 1, name: "Alterar senha" })).toBeVisible();

  await page.getByRole("button", { name: "Alterar senha", exact: true }).click();
  await expect(page.getByText("Informe a senha atual.")).toBeVisible();

  await page.getByLabel("Senha atual").fill("senha-atual-errada-123");
  await page.getByLabel("Nova senha", { exact: true }).fill("uma-senha-nova-bem-longa-1");
  await page.getByLabel("Repita a nova senha").fill("uma-senha-nova-bem-longa-1");
  await page.getByRole("button", { name: "Alterar senha", exact: true }).click();
  await expect(page.getByText("A senha atual está incorreta.")).toBeVisible();
  await expect(page.getByLabel("Nova senha", { exact: true })).toHaveValue("");

  // a sessao continua valida e a senha continua a mesma: o painel segue abrindo
  await page.goto("/painel");
  await expect(page.getByText(/Saldo em caixa em/)).toBeVisible();
});
