import { expect, test, type Page } from "@playwright/test";
import { BRUNO, CARLA, ELISA, PAULA, apiAcceptsThisOrigin, demoPassword, loginAs, tinyPdf, uniqueTag } from "./auth-helpers";

/**
 * TASK-011 (fatia 3): despesas contra a API REAL. Professora cria, anexa e envia; a gestao aprova (inclusive
 * parcial), reembolsa e paga. Precisa da API com o seed, do `npm run dev` na 3101 e da senha de demonstracao
 * (e2e/auth-helpers.ts). Cria despesas de demonstracao com descricao unica: o banco e compartilhado.
 * Serial e so no chromium-desktop: os passos dependem uns dos outros e criam dados. Os valores sao de
 * poucos centavos de real porque reembolsar e pagar LANCAM no caixa da escola de demonstracao (nao desfaz).
 */
test.describe.configure({ mode: "serial" });
test.skip(!demoPassword() || !apiAcceptsThisOrigin(), "precisa da senha de demonstracao e de E2E_BASE_URL=http://127.0.0.1:3101");
test.beforeEach(({}, testInfo) => {
  test.skip(testInfo.project.name !== "chromium-desktop", "cria dados: roda uma vez, em chromium-desktop");
});

const tag = uniqueTag("E2E despesa");
const apmTag = `${tag} APM`;

async function createExpense(page: Page, description: string, opts: { paidBy: "COLLABORATOR" | "APM"; cents: string; send: boolean }) {
  await page.getByRole("button", { name: "Nova despesa" }).click();
  await page.getByLabel("Valor", { exact: true }).fill(opts.cents);
  await page.getByLabel("Descrição").fill(description);
  await page.getByLabel("Motivo da compra").fill("Aula de artes do e2e");
  await page.getByLabel("Forma de pagamento").selectOption("PIX");
  await page.getByLabel(opts.paidBy === "APM" ? "Foi paga pela APM" : "Eu paguei do meu bolso (reembolso)").check();
  await page.getByLabel(/Nota ou recibo/).setInputFiles(tinyPdf(description));
  await page.getByRole("button", { name: opts.send ? "Salvar e enviar para análise" : "Salvar rascunho" }).click();
}

const row = (page: Page, text: string) => page.getByRole("listitem").filter({ hasText: text });

test("professora: so 'Minhas despesas'; cria, anexa e envia; baixa o anexo; ve o status em portugues", async ({ page }) => {
  await loginAs(page, PAULA.email); // a professora nao ve o resumo: o login cai em Minhas despesas
  await expect(page).toHaveURL(/\/painel\/despesas$/);
  await expect(page.getByRole("heading", { level: 1, name: "Minhas despesas" })).toBeVisible();
  // menu: so a propria area (o nome do usuario vem do login: nenhum campo de nome)
  await expect(page.getByRole("link", { name: "Resumo" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Fechamento" })).toHaveCount(0);

  await createExpense(page, tag, { paidBy: "COLLABORATOR", cents: "200", send: true });
  await expect(page.getByText("Despesa enviada para a análise da gestão.")).toBeVisible();
  const mine = row(page, tag);
  await expect(mine).toContainText("Enviada");
  await expect(mine).toContainText("Paga do bolso de quem enviou");

  // anexo pela API (download com a sessao, nunca URL publica)
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Baixar nota-e2e.pdf" }).click()]);
  expect(download.suggestedFilename()).toBe("nota-e2e.pdf");
  // enviada: sem editar nem anexar
  await expect(page.getByRole("button", { name: "Editar" })).toHaveCount(0);
  await expect(page.getByText(/aguardando a decisão da gestão/)).toBeVisible();
});

test("gestao: aprova PARCIALMENTE e registra o reembolso; o status vem da API", async ({ page }) => {
  await loginAs(page, CARLA.email);
  await page.goto("/painel/despesas");
  await expect(page.getByRole("link", { name: "Despesas", exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Nova despesa" })).toHaveCount(0);
  await row(page, tag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await page.getByRole("button", { name: "Aprovar", exact: true }).click();
  await page.getByLabel("Um valor menor (aprovação parcial)").check();
  await page.getByLabel("Valor aprovado").fill("150");
  await page.getByLabel("Motivo da diferença").fill("Sem o frete");
  await page.getByRole("button", { name: "Aprovar valor menor" }).click();
  await expect(page.getByText("Despesa aprovada.")).toBeVisible();

  // saiu da fila de "aguardando"; esta em "aprovadas", com o valor aprovado e o pedido, e reembolso pendente
  await page.getByRole("button", { name: "Aprovadas (a pagar)" }).click();
  const approved = row(page, tag);
  await expect(approved).toContainText(/R\$\s1,50 \(pedido: R\$\s2,00\)/);
  await expect(approved).toContainText("Reembolso pendente");
  await approved.getByRole("button", { name: /Abrir detalhes/ }).click();
  await page.getByRole("button", { name: "Registrar reembolso", exact: true }).click();
  await page.getByLabel("Referência do pagamento").fill(`PIX ${tag}`);
  await page.getByRole("button", { name: "Registrar reembolso", exact: true }).click();
  await expect(page.getByText("Reembolso registrado.")).toBeVisible();
  await page.getByRole("button", { name: "Pagas", exact: true }).click();
  const paid = row(page, tag);
  await expect(paid).toContainText("Paga");
  await paid.getByRole("button", { name: /Abrir detalhes/ }).click();
  await expect(paid).toContainText(`ref.: PIX ${tag}`);
});

test("despesa paga pela APM: aprova pelo valor pedido e registra o pagamento (sem opcao parcial)", async ({ page }) => {
  await loginAs(page, PAULA.email);
  await page.goto("/painel/despesas");
  await createExpense(page, apmTag, { paidBy: "APM", cents: "100", send: true });
  await expect(page.getByText("Despesa enviada para a análise da gestão.")).toBeVisible();

  await page.context().clearCookies();
  await loginAs(page, CARLA.email);
  await page.goto("/painel/despesas");
  await row(page, apmTag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await page.getByRole("button", { name: "Aprovar", exact: true }).click();
  await expect(page.getByLabel("Um valor menor (aprovação parcial)")).toHaveCount(0);
  await page.getByRole("button", { name: "Aprovar", exact: true }).click();
  await expect(page.getByText("Despesa aprovada.")).toBeVisible();
  await page.getByRole("button", { name: "Aprovadas (a pagar)" }).click();
  await row(page, apmTag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await expect(row(page, apmTag)).toContainText("Aprovada, a pagar");
  await expect(page.getByRole("button", { name: "Registrar reembolso", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "Registrar pagamento", exact: true }).click();
  await expect(page.getByText("Pagamento registrado.")).toBeVisible();
  await page.getByRole("button", { name: "Pagas", exact: true }).click();
  await expect(row(page, apmTag)).toContainText("Paga");
});

test("correcao pedida: a professora le o motivo da gestao e reenvia", async ({ page }) => {
  const fixTag = `${tag} corrigir`;
  await loginAs(page, PAULA.email);
  await page.goto("/painel/despesas");
  await createExpense(page, fixTag, { paidBy: "COLLABORATOR", cents: "100", send: true });
  await expect(page.getByText("Despesa enviada para a análise da gestão.")).toBeVisible();

  await page.context().clearCookies();
  await loginAs(page, CARLA.email);
  await page.goto("/painel/despesas");
  await row(page, fixTag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await page.getByRole("button", { name: "Pedir correção", exact: true }).click();
  await page.getByLabel("O que precisa ser corrigido").fill("Falta o CNPJ na nota");
  await page.getByRole("button", { name: "Pedir correção", exact: true }).click();
  await expect(page.getByText(/Correção pedida\. A despesa volta/)).toBeVisible();

  await page.context().clearCookies();
  await loginAs(page, PAULA.email);
  await page.goto("/painel/despesas");
  await row(page, fixTag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await expect(page.getByRole("note").filter({ hasText: "A gestão pediu correção: Falta o CNPJ na nota" })).toBeVisible();
  await expect(page.getByLabel(/Anexar outro arquivo/)).toBeVisible();
  await page.getByRole("button", { name: "Enviar para análise", exact: true }).click();
  await expect(page.getByText("Despesa enviada para a análise da gestão.")).toBeVisible();
  // deixa a fila limpa: a gestao recusa a despesa de teste
  await page.context().clearCookies();
  await loginAs(page, CARLA.email);
  await page.goto("/painel/despesas");
  await row(page, fixTag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await page.getByRole("button", { name: "Recusar", exact: true }).click();
  await page.getByLabel("Motivo da recusa").fill("Despesa de teste do e2e");
  await page.getByRole("button", { name: "Recusar despesa" }).click();
  await expect(page.getByText("Despesa recusada.")).toBeVisible();
});

test("o autor nao decide a propria despesa: o diretor ve o aviso e nenhum botao de decisao", async ({ page }) => {
  const ownTag = `${tag} do diretor`;
  await loginAs(page, BRUNO.email);
  await page.goto("/painel/despesas?aba=minhas");
  await createExpense(page, ownTag, { paidBy: "COLLABORATOR", cents: "100", send: true });
  await expect(page.getByText("Despesa enviada para a análise da gestão.")).toBeVisible();
  await page.goto("/painel/despesas");
  await row(page, ownTag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await expect(page.getByText("Esta despesa é sua: outra pessoa da gestão precisa decidir.")).toBeVisible();
  for (const name of ["Aprovar", "Recusar", "Pedir correção"]) await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
  // quem decide e a tesoureira; recusa para deixar a fila limpa
  await page.context().clearCookies();
  await loginAs(page, CARLA.email);
  await page.goto("/painel/despesas");
  await row(page, ownTag).getByRole("button", { name: /Abrir detalhes/ }).click();
  await page.getByRole("button", { name: "Recusar", exact: true }).click();
  await page.getByLabel("Motivo da recusa").fill("Despesa de teste do e2e");
  await page.getByRole("button", { name: "Recusar despesa" }).click();
  await expect(page.getByText("Despesa recusada.")).toBeVisible();
});

test("viewer: sem despesas, sem botoes de escrita", async ({ page }) => {
  await loginAs(page, ELISA.email);
  await expect(page.getByText(/Saldo em caixa em/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Despesas" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Minhas despesas" })).toHaveCount(0);
  await page.goto("/painel/despesas");
  await expect(page).toHaveURL(/\/painel$/);
});
