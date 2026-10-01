import { expect, test, type Page } from "@playwright/test";
import { applyScenario, findInvisibleText, type Scenario } from "./helpers";

/**
 * W9: em WebKit e Chromium, TODAS as telas e passos mostram o conteudo, mesmo com a linha do
 * tempo das animacoes congelada ou com o documento oculto. Falha se algum texto visivel tiver
 * opacidade efetiva < 0,9 apos 1,5 s (TASK-002, rodada 1: o passo 1 ficava invisivel no WebKit).
 */
const scenarios: Scenario[] = ["normal", "frozen", "hidden"];

test.use({ bypassCSP: true });

async function expectVisible(page: Page, label: string) {
  await page.waitForTimeout(1500);
  const bad = await findInvisibleText(page);
  expect(bad, `${label}: texto invisivel`).toEqual([]);
  const visibleChars = await page.evaluate(() => document.body.innerText.trim().length);
  expect(visibleChars, `${label}: pagina sem texto`).toBeGreaterThan(20);
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
        ["/apm/nao-existe", "404 da escola"],
        ["/pagina-que-nao-existe", "404 geral"],
      ] as const) {
        await page.goto(path);
        await expectVisible(page, label);
      }
    });

    test("portal: os 4 passos, o Pix, a confirmacao e o comprovante", async ({ page }) => {
      await page.goto("/apm/escola-exemplo");
      await expect(page.getByRole("heading", { name: "Quanto você quer contribuir?" })).toBeVisible();
      await expectVisible(page, "passo 1 (valor)");

      await page.getByRole("button", { name: "Continuar" }).click();
      await expectVisible(page, "passo 1 com erro");

      await page.getByText("Outro valor", { exact: true }).click();
      await expectVisible(page, "passo 1 com valor livre");
      await page.getByLabel("Valor da contribuição").fill("4500");
      await page.getByRole("button", { name: "Continuar" }).click();

      await expect(page.getByRole("heading", { name: "Quem está contribuindo?" })).toBeVisible();
      await expectVisible(page, "passo 2 (dados)");
      await page.getByRole("button", { name: "Continuar" }).click();
      await expectVisible(page, "passo 2 com erros");
      await page.getByLabel("Nome do responsável").fill("Ana Paula Lima");
      await page.getByLabel("Nome do aluno").fill("Davi Lima");
      await page.getByRole("button", { name: "Continuar" }).click();

      await expect(page.getByRole("heading", { name: "Confira antes de pagar" })).toBeVisible();
      await expectVisible(page, "passo 3 (revisao)");
      await page.getByRole("button", { name: /Gerar Pix/ }).click();

      await expect(page.getByRole("heading", { name: "Pague com Pix" })).toBeVisible();
      await expectVisible(page, "passo 4 (Pix)");

      // o mock devolve PAID depois de ~8 s; documento oculto pode atrasar os timers
      await expect(page.getByRole("heading", { name: "Pagamento confirmado" })).toBeVisible({ timeout: 40_000 });
      await expectVisible(page, "pagamento confirmado");

      await page.getByRole("link", { name: "Ver comprovante" }).click();
      await expect(page.getByRole("heading", { name: "Contribuição confirmada" })).toBeVisible();
      await expectVisible(page, "comprovante");
    });

    test("portal: outras escolas e comprovante de exemplo", async ({ page }) => {
      for (const [path, label] of [
        ["/apm/escola-horizonte", "escola Horizonte"],
        ["/apm/emei-vale-verde", "EMEI Vale Verde"],
        ["/apm/escola-exemplo/pedido/demo-comprovante-0001", "comprovante de exemplo"],
      ] as const) {
        await page.goto(path);
        await expectVisible(page, label);
      }
    });

    test("painel: resumo, abas, seletor e estados de carregamento e vazio", async ({ page }) => {
      await page.goto("/painel");
      await expectVisible(page, "painel resumo");
      for (const tipo of ["contribuicoes", "despesas", "reembolsos", "devolucoes"]) {
        await page.goto(`/painel?tipo=${tipo}`);
        await expectVisible(page, `painel ${tipo}`);
      }
      await page.goto("/painel");
      await page.getByRole("button", { name: /Escola Exemplo/ }).first().click();
      await expect(page.getByRole("button", { name: /EMEI Vale Verde/ })).toBeVisible();
      await expectVisible(page, "seletor de escola aberto");
      await page.getByRole("button", { name: /EMEI Vale Verde/ }).click();
      await expect(page.getByRole("heading", { name: /Nenhuma movimentação/ })).toBeVisible();
      await expectVisible(page, "painel vazio");
    });
  });
}
