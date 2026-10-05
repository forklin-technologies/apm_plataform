import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * N4: a barra superior translucida precisa funcionar no WebKit sem blur (WKWebView, portal do
 * Maestri): alpha de base >= 0,9, para nada passar por baixo e colidir com o texto; e fundo solido
 * quando o usuario pede menos transparencia ou o navegador nao suporta backdrop-filter.
 */
const css = readFileSync(join(__dirname, "globals.css"), "utf8");

describe("barra translucida (.bar-material)", () => {
  const alphas = [...css.matchAll(/--bar:\s*rgb\([^)]*\/\s*([\d.]+)\)/g)].map((m) => Number(m[1]));

  it("define --bar nos temas claro e escuro, ambos com alpha >= 0,9", () => {
    expect(alphas).toHaveLength(2);
    for (const alpha of alphas) expect(alpha).toBeGreaterThanOrEqual(0.9);
  });

  it("tem fundo solido de reserva sem backdrop-filter e com prefers-reduced-transparency", () => {
    expect(css).toMatch(/@supports not \(\(backdrop-filter: blur\(1px\)\) or \(-webkit-backdrop-filter: blur\(1px\)\)\)/);
    expect(css).toMatch(/@media \(prefers-reduced-transparency: reduce\)\s*\{\s*\.bar-material\s*\{[^}]*background:\s*var\(--surface\)/);
  });

  it("usa o prefixo -webkit- junto do backdrop-filter (Safari)", () => {
    expect(css).toMatch(/-webkit-backdrop-filter:\s*saturate\(180%\) blur\(22px\)/);
  });
});
