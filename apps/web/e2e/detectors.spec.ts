import { expect, test } from "@playwright/test";
import { findInvisibleStampParts, findInvisibleText } from "./helpers";

/** Teste dos DETECTORES (N6): cada forma de esconder conteudo precisa ser pega, e o legitimo nao. */
const stamp = (partStyle: string) => `
  <body><div data-testid="paid-stamp"><svg viewBox="0 0 120 120" width="112" height="112" fill="none">
    <circle data-part="ring" cx="60" cy="60" r="52" stroke="green" stroke-width="3" style="${partStyle}"/>
    <path data-part="check" d="M38 62l14 14 30-33" stroke="green" stroke-width="7"/>
  </svg></div></body>`;

test.describe("detector do carimbo SVG", () => {
  test("carimbo visivel: nada a reportar", async ({ page }) => {
    await page.setContent(stamp(""));
    expect(await findInvisibleStampParts(page)).toEqual([]);
  });

  for (const [name, style, expected] of [
    ["tracado escondido (dashoffset = dasharray)", "stroke-dasharray:1;stroke-dashoffset:1", /tracado escondido/],
    ["opacidade 0", "opacity:0", /opacidade efetiva/],
    ["stroke-opacity 0", "stroke-opacity:0", /stroke-opacity/],
    ["escala 0", "transform:scale(0.01);transform-box:fill-box;transform-origin:center", /escala/],
    ["stroke none", "stroke:none", /sem stroke/],
  ] as const) {
    test(`pega: ${name}`, async ({ page }) => {
      await page.setContent(stamp(style));
      const problems = await findInvisibleStampParts(page);
      expect(problems.join(" | ")).toMatch(expected);
    });
  }

  test("pega carimbo ausente e partes faltando", async ({ page }) => {
    await page.setContent("<body><p>sem carimbo</p></body>");
    expect(await findInvisibleStampParts(page)).toEqual(["carimbo ausente"]);
  });

  test("a ondulacao decorativa (data-decorative) nao conta", async ({ page }) => {
    await page.setContent(`<body><div data-testid="paid-stamp"><svg viewBox="0 0 120 120" width="112" height="112" fill="none">
      <circle data-decorative="true" cx="60" cy="60" r="50" stroke="green" style="opacity:0"/>
      <circle data-part="ring" cx="60" cy="60" r="52" stroke="green" stroke-width="3"/>
      <path data-part="check" d="M38 62l14 14 30-33" stroke="green" stroke-width="7"/></svg></div></body>`);
    expect(await findInvisibleStampParts(page)).toEqual([]);
  });
});

test.describe("detector de texto invisivel", () => {
  for (const [name, style] of [
    ["opacity: 0", "opacity:0"],
    ["opacity: 0.05", "opacity:0.05"],
    ["opacidade herdada do ancestral", "opacity:0.3"],
    ["visibility: hidden", "visibility:hidden"],
  ] as const) {
    test(`pega: ${name}`, async ({ page }) => {
      await page.setContent(`<body><section style="${style}"><h2>Titulo escondido</h2></section></body>`);
      expect((await findInvisibleText(page)).join(" ")).toContain("Titulo escondido");
    });
  }

  test("nao acusa o legitimo: sr-only, display:none, desativado e opacidade alta", async ({ page }) => {
    await page.setContent(`<body>
      <p style="opacity:1">visivel</p><p style="opacity:0.95">quase opaco</p>
      <span class="sr-only" style="opacity:0">so leitor de tela</span>
      <p style="display:none">oculto por display</p>
      <button disabled style="opacity:.45">Entrar</button>
      <details><summary>Resumo</summary><p>conteudo fechado</p></details></body>`);
    expect(await findInvisibleText(page)).toEqual([]);
  });
});
