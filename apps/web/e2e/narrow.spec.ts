import { expect, test, type Page } from "@playwright/test";
import { apiAcceptsThisOrigin, demoPassword, loginAs } from "./auth-helpers";

/**
 * N5 e N4: telas estreitas (320 e 360 px) sem rolagem horizontal nem rotulo truncado, payload do Pix
 * que quebra entre palavras, cabecalho da entrada que nao pula, e barra superior que nao deixa o
 * conteudo colidir por baixo.
 */
const routes = [
  "/",
  "/escola/demo-aurora",
  "/escola/demo-horizonte",
  "/login",
  "/accept-invitation",
  "/accept-invitation?token=abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
  "/escola/nao-existe",
];

// O painel exige sessao: estas rotas so rodam com a API real e a senha de demonstracao (e2e/auth-helpers.ts).
const painelRoutes = ["/painel", "/painel?tipo=contribuicoes", "/painel?tipo=despesas", "/painel?tipo=reembolsos", "/painel?tipo=devolucoes"];

async function overflow(page: Page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const outside: string[] = [];
    for (const el of document.querySelectorAll("body *")) {
      if (el.closest("[class*='sr-only']") || el.closest("svg")) continue;
      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.right > vw + 1 || r.left < -1) outside.push(`${el.tagName.toLowerCase()} ${Math.round(r.left)}..${Math.round(r.right)} "${(el.textContent ?? "").trim().slice(0, 24)}"`);
    }
    return { scroll: document.documentElement.scrollWidth - vw, outside: outside.slice(0, 5) };
  });
}

// Precisa da API real (o servidor busca a escola e o POST cria a contribuicao): E2E_BASE_URL=http://127.0.0.1:3101.
async function reachPix(page: Page) {
  await page.goto("/escola/demo-aurora");
  await page.getByRole("radio", { name: /20,00/ }).check({ force: true });
  await page.getByRole("button", { name: "Continuar" }).click();
  await page.getByRole("button", { name: "Continuar" }).click(); // dados opcionais em branco
  await page.getByRole("button", { name: /Gerar Pix/ }).click();
  await expect(page.getByRole("heading", { name: "Pague com Pix" })).toBeVisible();
}

for (const width of [320, 360]) {
  test.describe(`${width}px`, () => {
    test.use({ viewport: { width, height: 740 } });

    test("sem rolagem horizontal e sem elemento fora da borda em todas as rotas", async ({ page }) => {
      for (const route of routes) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        const o = await overflow(page);
        expect(o, route).toEqual({ scroll: 0, outside: [] });
      }
    });

    test("sem rolagem horizontal e sem elemento fora da borda no painel (logado)", async ({ page }) => {
      test.skip(!demoPassword(), "sem E2E_DEMO_PASSWORD_FILE/E2E_DEMO_PASSWORD");
      await loginAs(page);
      for (const route of painelRoutes) {
        await page.goto(route);
        await page.waitForLoadState("networkidle");
        const o = await overflow(page);
        expect(o, route).toEqual({ scroll: 0, outside: [] });
      }
    });

    test("barra de abas do painel: nenhum rotulo truncado (nem 'Contribuições')", async ({ page }) => {
      test.skip(!demoPassword(), "sem E2E_DEMO_PASSWORD_FILE/E2E_DEMO_PASSWORD");
      await loginAs(page);
      const labels = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("nav[aria-label='Seções do painel'] a > span:last-child")].map((el) => ({
          text: el.textContent,
          truncated: el.scrollWidth > el.clientWidth + 0.5 || getComputedStyle(el).textOverflow === "ellipsis",
          fits: el.getBoundingClientRect().right <= document.documentElement.clientWidth,
        })),
      );
      expect(labels.map((l) => l.text)).toEqual(["Resumo", "Contribuições", "Despesas", "Reembolsos", "Devoluções"]);
      for (const label of labels) {
        expect(label.truncated, `${label.text} truncado`).toBe(false);
        expect(label.fits, `${label.text} fora da tela`).toBe(true);
      }
    });

    test("cartao 'Saldo em caixa' do painel cabe e o numero nao e cortado", async ({ page }) => {
      test.skip(!demoPassword(), "sem E2E_DEMO_PASSWORD_FILE/E2E_DEMO_PASSWORD");
      await loginAs(page);
      await expect(page.getByText(/Saldo em caixa em/)).toBeVisible();
      const fit = await page.evaluate(() => {
        const card = document.querySelector("#indicators-title")!.closest("section")!;
        const number = [...card.querySelectorAll("p")].find((p) => /^R\$\s?[\d.]+,\d{2}$/.test((p.textContent ?? "").trim()))!;
        return { numberRight: number.getBoundingClientRect().right, cardRight: card.getBoundingClientRect().right, scroll: number.scrollWidth - number.clientWidth, vw: document.documentElement.clientWidth };
      });
      expect(fit.scroll).toBeLessThanOrEqual(0);
      expect(fit.numberRight).toBeLessThanOrEqual(fit.cardRight);
      expect(fit.cardRight).toBeLessThanOrEqual(fit.vw);
    });

    test("QR e payload do Pix cabem na tela: sem rolagem horizontal e sem linhas escondidas na caixa do codigo", async ({ page }) => {
      test.skip(!apiAcceptsThisOrigin(), "precisa da API real (E2E_BASE_URL=http://127.0.0.1:3101)");
      await reachPix(page);
      const result = await page.evaluate(() => {
        const textarea = document.querySelector<HTMLTextAreaElement>("#pix-code")!;
        const qr = document.querySelector("svg[role=img]")!.getBoundingClientRect();
        return {
          value: textarea.value,
          overflowX: textarea.scrollWidth - textarea.clientWidth,
          clippedY: textarea.scrollHeight - textarea.clientHeight,
          qrRight: qr.right,
          qrWidth: qr.width,
          vw: document.documentElement.clientWidth,
          pageOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        };
      });
      expect(result.value).toMatch(/^PIX-SANDBOX:/);
      expect(result.overflowX).toBeLessThanOrEqual(0);
      expect(result.clippedY, "linhas do payload escondidas na caixa").toBeLessThanOrEqual(1);
      expect(result.qrRight).toBeLessThanOrEqual(result.vw);
      expect(result.qrWidth, "QR pequeno demais para ler").toBeGreaterThanOrEqual(200);
      expect(result.pageOverflow).toBeLessThanOrEqual(0);
    });
  });
}

test.describe("cabecalho da entrada nao pula com a API verificando, no ar, fora do ar e nao pronta", () => {
  for (const width of [320, 360, 390, 1440]) {
    test(`${width}px`, async ({ page }) => {
      await page.setViewportSize({ width, height: 800 });
      let mode: "hold" | "ready" | "down" | "not-ready" = "hold";
      const held: Array<() => void> = [];
      await page.route("**/api/health/ready", async (route) => {
        if (mode === "hold") await new Promise<void>((resolve) => held.push(resolve));
        if (mode === "down") return route.fulfill({ status: 500, contentType: "text/plain", body: "Internal Server Error" });
        if (mode === "not-ready") return route.fulfill({ status: 503, contentType: "application/json", body: '{"status":"unavailable"}' });
        return route.fulfill({ status: 200, contentType: "application/json", body: '{"status":"ready"}' });
      });
      await page.goto("/");

      const layout = async () =>
        page.evaluate(() => ({
          header: Math.round(document.querySelector("header")!.getBoundingClientRect().height * 10) / 10,
          h1: Math.round(document.querySelector("h1")!.getBoundingClientRect().top * 10) / 10,
          main: Math.round(document.querySelector("main")!.getBoundingClientRect().top * 10) / 10,
          scroll: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        }));
      const state = () => page.locator("[role=status][data-state]").first();

      await expect(state()).toHaveAttribute("data-state", "checking");
      const checking = await layout();

      mode = "ready";
      held.splice(0).forEach((release) => release());
      await expect(state()).toHaveAttribute("data-state", "ready");
      const ready = await layout();

      mode = "down";
      await page.evaluate(() => window.dispatchEvent(new Event("online")));
      await expect(state()).toHaveAttribute("data-state", "unavailable");
      await expect(page.getByRole("button", { name: "Tentar de novo" })).toBeVisible();
      const down = await layout();

      mode = "not-ready";
      await page.getByRole("button", { name: "Tentar de novo" }).click();
      await expect(state()).toContainText("API indisponível");
      const notReady = await layout();

      for (const [name, l] of [["no ar", ready], ["fora do ar", down], ["nao pronta", notReady]] as const) {
        expect(l, `layout ${name} x verificando`).toEqual(checking);
      }
      expect(checking.scroll).toBeLessThanOrEqual(0);
      // o botao de tentar de novo continua com alvo de 44px mesmo so com o icone
      const box = await page.getByRole("button", { name: "Tentar de novo" }).boundingBox();
      expect(box!.width).toBeGreaterThanOrEqual(43.5);
      expect(box!.height).toBeGreaterThanOrEqual(43.5);
    });
  }
});

test.describe("N4: barra superior translucida", () => {
  for (const scheme of ["light", "dark"] as const) {
    test(`alpha de base >= 0,9 (${scheme}) no portal da escola e no painel`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      const routes = ["/escola/demo-aurora"];
      if (demoPassword()) {
        await loginAs(page);
        routes.push("/painel");
      }
      for (const route of routes) {
        await page.goto(route);
        const alphas = await page.evaluate(() =>
          // barras de navegacao (topo, abas, lateral); a barra de acoes do formulario e transparente no desktop de proposito
          [...document.querySelectorAll("header.bar-material, nav.bar-material, aside.bar-material")].map((el) => {
            const m = /rgba?\(([^)]+)\)/.exec(getComputedStyle(el).backgroundColor);
            const parts = m![1]!.split(/[,\s/]+/).filter(Boolean);
            return parts.length >= 4 ? parseFloat(parts[3]!) : 1;
          }),
        );
        expect(alphas.length, route).toBeGreaterThan(0);
        for (const alpha of alphas) expect(alpha, `${route} ${scheme}`).toBeGreaterThanOrEqual(0.9);
      }
    });
  }
});
