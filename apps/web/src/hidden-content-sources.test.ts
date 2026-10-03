import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * N6: o teste de CSS (src/app/motion-css.test.ts) so enxerga globals.css. Esta guarda varre os
 * componentes atras de utilitarios Tailwind que escondem conteudo (opacity-0, invisible, scale-0,
 * translate para fora, clip-path, content-visibility, tamanho 0) e do uso do MotionGate no layout.
 */
const SRC = __dirname;

function sourceFiles(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) sourceFiles(full, out);
    else if (/\.tsx$/.test(name) && !/\.test\.tsx$/.test(name)) out.push(full);
  }
  return out;
}

/** Utilitarios (com qualquer variante: hover:, lg:, group-has-[...]:) que escondem conteudo. */
const HIDING = /(?:^|[\s"'`:])(?:[a-z0-9\-\[\]():_>,=*&]+:)*(opacity-0|opacity-\[0(?:\.0+)?%?\]|invisible|scale-0|scale-[xyz]-0|-?translate-[xy]-full|\[clip-path:[^\]]+\]|clip-\[[^\]]+\]|content-hidden|\[content-visibility:hidden\]|(?:size|h|w|max-h|max-w)-0)(?=[\s"'`]|$)/g;

/** Usos que escondem DE PROPOSITO, com o motivo. Qualquer outro e defeito. */
const ALLOWED: Array<{ file: RegExp; token: string; why: string }> = [
  { file: /portal\/AmountStep\.tsx$/, token: "opacity-0", why: "check do radio nao selecionado: so aparece com :checked, sem animacao" },
];

describe("utilitarios Tailwind que escondem conteudo", () => {
  it("nenhum componente esconde conteudo fora da allowlist", () => {
    const offenders: string[] = [];
    for (const file of sourceFiles(SRC)) {
      const source = readFileSync(file, "utf8");
      for (const match of source.matchAll(HIDING)) {
        const token = match[1]!;
        const allowed = ALLOWED.some((a) => a.file.test(file) && a.token === token);
        if (!allowed) offenders.push(`${file.replace(SRC, "src")}: ${token}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("a allowlist nao esta obsoleta (cada excecao ainda existe no codigo)", () => {
    for (const entry of ALLOWED) {
      const file = sourceFiles(SRC).find((f) => entry.file.test(f));
      expect(file, entry.why).toBeTruthy();
      expect(readFileSync(file!, "utf8")).toContain(entry.token);
    }
  });

  it("o detector pega o que deve (mutacao)", () => {
    const hits = (src: string) => [...src.matchAll(HIDING)].map((m) => m[1]);
    expect(hits('<div className="opacity-0 transition" />')).toEqual(["opacity-0"]);
    expect(hits('<div className="lg:invisible" />')).toEqual(["invisible"]);
    expect(hits('<div className="group-has-[:checked]:scale-0" />')).toEqual(["scale-0"]);
    expect(hits('<div className="-translate-y-full" />')).toEqual(["-translate-y-full"]);
    expect(hits('<div className="[clip-path:inset(50%)]" />')).toEqual(["[clip-path:inset(50%)]"]);
    expect(hits('<div className="opacity-[0]" />')).toEqual(["opacity-[0]"]);
    expect(hits('<div className="h-0 overflow-hidden" />')).toEqual(["h-0"]);
    // legitimos
    expect(hits('<div className="min-w-0 flex-1 opacity-100 lg:hidden sr-only" />')).toEqual([]);
    expect(hits('<div className="opacity-45 disabled:opacity-45 h-04" />')).toEqual([]);
  });
});

describe("MotionGate no layout raiz", () => {
  const layout = readFileSync(join(SRC, "app", "layout.tsx"), "utf8");

  it("e importado e montado dentro do <body>, antes dos filhos", () => {
    expect(layout).toMatch(/import \{ MotionGate \} from "@\/components\/ui\/MotionGate"/);
    const body = layout.slice(layout.indexOf("<body>"), layout.indexOf("</body>"));
    expect(body).toContain("<MotionGate />");
    expect(body.indexOf("<MotionGate />")).toBeLessThan(body.indexOf("{children}"));
  });

  it("globals.css e o layout compartilham o atributo: o CSS so anima sob [data-motion=\"on\"] e o gate liga esse atributo", () => {
    const gate = readFileSync(join(SRC, "components", "ui", "MotionGate.tsx"), "utf8");
    expect(gate).toContain("dataset.motion");
    expect(gate).toContain('"on"');
    expect(readFileSync(join(SRC, "app", "globals.css"), "utf8")).toContain('[data-motion="on"]');
  });
});
