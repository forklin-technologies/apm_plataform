import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseCss, scanHiddenContent, scanUngatedAnimations, walk } from "@/test-utils/css-guard";

/**
 * Guarda contra a classe de defeito "conteudo invisivel porque a animacao nao rodou" (TASK-002,
 * rodada 1). O WebKit congela a linha do tempo das animacoes com o documento oculto (portal do
 * Maestri, aba em segundo plano): uma animacao de entrada parada em t=0 deixa o conteudo no
 * keyframe inicial para sempre. Regras (N6 amplia a classe: nao so opacity:0):
 *   1. nenhum @keyframes parte de algo que esconda: opacity <= 0.05 (inclui 0%), visibility:hidden,
 *      escala < 0.1, translate para fora da tela, clip-path, content-visibility:hidden, tamanho 0;
 *   2. o mesmo vale para o estado BASE de qualquer regra (so os seletores da allowlist, que
 *      escondem de proposito, ficam de fora);
 *   3. animacao com fill-mode both/backwards so existe sob [data-motion="on"] (MotionGate);
 *   4. as unicas animacoes fora do gate sao infinitas e decorativas (shimmer, pulse-dot).
 * Os testes de mutacao provam que cada regra detecta o defeito de verdade.
 */
const css = readFileSync(join(__dirname, "globals.css"), "utf8");

describe("globals.css: animacoes sao melhoria progressiva", () => {
  it("encontra os blocos de animacao (sanidade do parser)", () => {
    const names: string[] = [];
    walk(parseCss(css), (b) => {
      if (b.head.startsWith("@keyframes")) names.push(b.head.replace("@keyframes", "").trim());
    });
    expect(names).toEqual(expect.arrayContaining(["rise-in", "pop", "stamp-in", "stamp-ring", "stamp-check", "stamp-ripple", "shimmer", "pulse-dot"]));
  });

  it("nada esconde conteudo no keyframe inicial nem no estado base", () => {
    expect(scanHiddenContent(css)).toEqual([]);
  });

  it("fill-mode both/backwards so sob [data-motion=\"on\"]; fora do gate so animacao infinita e decorativa", () => {
    const { fillOutsideGate, ungated } = scanUngatedAnimations(css);
    expect(fillOutsideGate).toEqual([]);
    for (const entry of ungated) expect(entry).toMatch(/infinite/);
    expect(ungated.map((e) => e.split(" =>")[0]!.trim()).sort()).toEqual([".pulse-dot", ".skeleton"]);
  });

  it("o carimbo e desenhado por escala, nunca por tracado (stroke-dashoffset sumiria o simbolo se congelasse)", () => {
    const code = css.replace(/\/\*[\s\S]*?\*\//g, ""); // sem comentarios
    expect(code).not.toMatch(/stroke-dashoffset/);
    expect(code).not.toMatch(/stroke-dasharray/);
    expect(code).toMatch(/\.stamp\s*\{[^}]*rotate\(-8deg\)/);
  });
});

describe("N6: teste de mutacao: cada defeito reintroduzido e detectado", () => {
  const keyframe = (decl: string) => `@keyframes enter { from { ${decl} } to { opacity: 1; transform: none; } } .x { animation: enter 1s; }`;
  const base = (decl: string) => `.step-in { ${decl} }`;

  it.each([
    ["opacity: 0 (o defeito original)", keyframe("opacity: 0; transform: translateY(8px);")],
    ["opacity: 0%", keyframe("opacity: 0%;")],
    ["opacity: 0.0", keyframe("opacity: 0.0;")],
    ["opacity: .02", keyframe("opacity: .02;")],
    ["quadro 0% em vez de from", "@keyframes enter { 0% { opacity: 0; } 100% { opacity: 1; } }"],
    ["visibility: hidden", keyframe("visibility: hidden;")],
    ["scale(0)", keyframe("transform: scale(0);")],
    ["scale(0.05) rotate", keyframe("transform: rotate(-8deg) scale(0.05);")],
    ["scale: 0", keyframe("scale: 0;")],
    ["translateY(100vh)", keyframe("transform: translateY(100vh);")],
    ["translateX(-100%)", keyframe("transform: translateX(-100%);")],
    ["translateX(-200px)", keyframe("transform: translateX(-200px);")],
    ["translate: 0 -500px", keyframe("translate: 0 -500px;")],
    ["clip-path: inset(100%)", keyframe("clip-path: inset(100%);")],
    ["height: 0", keyframe("height: 0;")],
    ["content-visibility: hidden no estado base", base("content-visibility: hidden;")],
    ["opacity: 0 no estado base", base("opacity: 0;")],
    ["visibility: hidden no estado base", base("visibility: hidden;")],
    ["clip-path no estado base", base("clip-path: inset(50%);")],
    ["scale(0) no estado base", base("transform: scale(0);")],
    ["translate fora da tela no estado base", base("transform: translateY(-100%);")],
  ])("detecta %s", (_name, mutated) => {
    expect(scanHiddenContent(mutated).length).toBeGreaterThan(0);
  });

  it.each([
    ["translateY(8px)", keyframe("transform: translateY(8px);")],
    ["scale(.94)", keyframe("transform: scale(.94);")],
    ["rotate + scale(1.7)", keyframe("transform: scale(1.7) rotate(-18deg);")],
    ["opacity: .45", keyframe("opacity: .45;")],
  ])("nao acusa o que e legitimo: %s", (_name, ok) => {
    expect(scanHiddenContent(ok)).toEqual([]);
  });

  it("a allowlist cobre so decorativos: .stamp-ripple e .sr-only-live", () => {
    expect(scanHiddenContent(".stamp-ripple { opacity: 0; } .sr-only-live { clip-path: inset(50%); }")).toEqual([]);
    expect(scanHiddenContent(".card { opacity: 0; }")).toHaveLength(1);
  });

  it("fill-mode both fora do gate e animacao de entrada fora do gate sao detectados", () => {
    const ungated = scanUngatedAnimations(".step-in { animation: rise-in 240ms ease both; }");
    expect(ungated.fillOutsideGate).toHaveLength(1);
    expect(ungated.ungated).toHaveLength(1);
    expect(ungated.ungated[0]).not.toMatch(/infinite/); // o teste real exige 'infinite' fora do gate
    const gated = scanUngatedAnimations('[data-motion="on"] .step-in { animation: rise-in 240ms ease backwards; }');
    expect(gated.fillOutsideGate).toEqual([]);
    expect(gated.ungated).toEqual([]);
  });
});
