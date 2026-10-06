import { expect, test, type Page } from "@playwright/test";
import { ANA, apiAcceptsThisOrigin, demoPassword, loginAs } from "./auth-helpers";
import {
  applyScenario,
  cspViolations,
  entranceAnimations,
  findInvisibleStampParts,
  findInvisibleText,
  motionAttribute,
  type Scenario,
} from "./helpers";

/**
 * W9: em WebKit e Chromium, TODAS as telas e passos mostram o conteudo, mesmo com a linha do
 * tempo das animacoes congelada ou com o documento oculto. Falha se algum texto visivel tiver
 * opacidade efetiva < 0,9 apos 1,5 s (TASK-002, rodada 1: o passo 1 ficava invisivel no WebKit).
 */
const scenarios: Scenario[] = ["normal", "frozen", "hidden"];

// SEM bypassCSP: a CSP real do app esta ativa em todos os cenarios (N6). Qualquer violacao falha o teste.

async function expectVisible(page: Page, label: string, scenario: Scenario) {
  await page.waitForTimeout(1500);
  const bad = await findInvisibleText(page);
  expect(bad, `${label}: texto invisivel`).toEqual([]);
  const visibleChars = await page.evaluate(() => document.body.innerText.trim().length);
  expect(visibleChars, `${label}: pagina sem texto`).toBeGreaterThan(20);
  expect(await cspViolations(page), `${label}: violacoes de CSP`).toEqual([]);

  // O MotionGate (montado no layout) liga data-motion so com o documento visivel. No cenario
  // 'hidden' nenhuma animacao de entrada pode existir (senao o conteudo dependeria de uma animacao
  // que o WebKit congelou).
  if (scenario === "hidden") {
    expect(await motionAttribute(page), `${label}: data-motion deve estar DESLIGADO com o documento oculto`).toBeUndefined();
    expect(await entranceAnimations(page), `${label}: animacao de entrada com o documento oculto`).toEqual([]);
  } else {
    expect(await motionAttribute(page), `${label}: MotionGate nao ligou data-motion (nao esta no layout?)`).toBe("on");
  }
}

for (const scenario of scenarios) {
  test.describe(`conteudo visivel [${scenario}]`, () => {
    test.beforeEach(async ({ page }) => {
      await applyScenario(page, scenario);
    });

    test("entrada, login e as duas 404", async ({ page }) => {
      for (const [path, label] of [
        ["/", "entrada"],
        ["/login", "login"],
        ["/accept-invitation", "convite sem token"],
        ["/accept-invitation?token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG", "convite"],
        ["/escola/nao-existe", "404 da escola"],
        ["/pagina-que-nao-existe", "404 geral"],
      ] as const) {
        await page.goto(path);
        await expectVisible(page, label, scenario);
      }
    });

    test("portal (API real): os passos, o QR, a confirmacao e o comprovante", async ({ page }) => {
      test.skip(!apiAcceptsThisOrigin(), "precisa da API real (E2E_BASE_URL=http://127.0.0.1:3101)");
      await page.goto("/escola/demo-aurora");
      await expect(page.getByRole("heading", { name: "Quanto você quer contribuir?" })).toBeVisible();
      await expectVisible(page, "passo 1 (valor)", scenario);

      await page.getByRole("button", { name: "Continuar" }).click();
      await expectVisible(page, "passo 1 com erro", scenario);

      await page.getByText("Outro valor", { exact: true }).click();
      await expectVisible(page, "passo 1 com valor livre", scenario);
      await page.getByLabel("Valor da contribuição").fill("4500");
      await page.getByRole("button", { name: "Continuar" }).click();

      await expect(page.getByRole("heading", { name: "Quem está contribuindo?" })).toBeVisible();
      await expectVisible(page, "passo 2 (dados)", scenario);
      await page.getByLabel(/E-mail/).fill("sem-arroba");
      await page.getByRole("button", { name: "Continuar" }).click();
      await expectVisible(page, "passo 2 com erro", scenario);
      await page.getByLabel(/E-mail/).fill("");
      await page.getByLabel(/Nome do responsável/).fill("Ana Paula Lima");
      await page.getByRole("button", { name: "Continuar" }).click();

      await expect(page.getByRole("heading", { name: "Confira antes de pagar" })).toBeVisible();
      await expectVisible(page, "passo 3 (revisao)", scenario);
      await page.getByRole("button", { name: /Gerar Pix/ }).click();

      await expect(page.getByRole("heading", { name: "Pague com Pix" })).toBeVisible();
      await expectVisible(page, "passo 4 (Pix)", scenario);

      await page.getByRole("button", { name: "Simular pagamento (somente desenvolvimento)" }).click();
      await expect(page.getByRole("heading", { name: "Pagamento confirmado" })).toBeVisible({ timeout: 40_000 });
      await expectVisible(page, "pagamento confirmado", scenario);
      // N6: o carimbo SVG (anel e check) tambem precisa estar visivel, mesmo congelado em t=0
      expect(await findInvisibleStampParts(page), "carimbo: partes invisiveis").toEqual([]);

      await page.getByRole("link", { name: "Ver comprovante" }).click();
      await expect(page.getByRole("heading", { name: "Contribuição confirmada" })).toBeVisible();
      await expectVisible(page, "comprovante", scenario);
    });

    test("portal (API real): outras escolas", async ({ page }) => {
      test.skip(!apiAcceptsThisOrigin(), "precisa da API real (E2E_BASE_URL=http://127.0.0.1:3101)");
      for (const [path, label] of [
        ["/escola/demo-horizonte", "escola Horizonte"],
        ["/escola/demo-central", "escola Central"],
      ] as const) {
        await page.goto(path);
        await expectVisible(page, label, scenario);
      }
    });

    test("painel (logado): resumo, abas e seletor de vinculos", async ({ page }) => {
      test.skip(!demoPassword(), "sem E2E_DEMO_PASSWORD_FILE/E2E_DEMO_PASSWORD");
      await loginAs(page);
      await expectVisible(page, "painel resumo", scenario);
      for (const tipo of ["contribuicoes", "despesas", "reembolsos", "devolucoes"]) {
        await page.goto(`/painel?tipo=${tipo}`);
        await expectVisible(page, `painel ${tipo}`, scenario);
      }
      await page.goto("/painel");
      await page.getByRole("button", { name: new RegExp(ANA.organization) }).first().click();
      await expect(page.getByRole("group", { name: ANA.organization })).toBeVisible();
      await expectVisible(page, "seletor de vinculos aberto", scenario);
    });
  });
}
