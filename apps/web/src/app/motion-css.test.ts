import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Guarda contra a classe de defeito "conteudo invisivel porque a animacao nao rodou" (TASK-002,
 * rodada 1). O WebKit congela a linha do tempo das animacoes com o documento oculto (portal do
 * Maestri, aba em segundo plano): uma animacao de entrada parada em t=0 deixa o conteudo no
 * keyframe inicial para sempre. Por isso:
 *   1. nenhum @keyframes parte de opacity: 0;
 *   2. animacao com fill-mode both/backwards so existe sob [data-motion="on"] (MotionGate);
 *   3. as unicas animacoes fora do gate sao infinitas e decorativas (shimmer, pulse-dot).
 */

interface Block {
  head: string;
  body: string;
  children: Block[];
}

function parse(css: string): Block[] {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, "");
  let i = 0;
  function readBlocks(): Block[] {
    const out: Block[] = [];
    let head = "";
    let decl = "";
    while (i < src.length) {
      const ch = src[i++]!;
      if (ch === "{") {
        const children = readBlocks();
        out.push({ head: head.trim(), body: "", children });
        head = "";
        decl = "";
      } else if (ch === "}") {
        const trimmed = (head + decl).trim();
        if (trimmed) out.push({ head: "", body: trimmed, children: [] });
        return out;
      } else if (ch === ";") {
        out.push({ head: "", body: (head + decl).trim(), children: [] });
        head = "";
        decl = "";
      } else {
        head += ch;
      }
    }
    return out;
  }
  return readBlocks();
}

function walk(blocks: Block[], visit: (b: Block, parents: Block[]) => void, parents: Block[] = []) {
  for (const b of blocks) {
    visit(b, parents);
    walk(b.children, visit, [...parents, b]);
  }
}

const css = readFileSync(join(__dirname, "globals.css"), "utf8");
const tree = parse(css);

const declarations = (b: Block) => b.children.filter((c) => c.head === "").map((c) => c.body);

describe("globals.css: animacoes sao melhoria progressiva", () => {
  it("encontra os blocos de animacao (sanidade do parser)", () => {
    const names: string[] = [];
    walk(tree, (b) => {
      if (b.head.startsWith("@keyframes")) names.push(b.head.replace("@keyframes", "").trim());
    });
    expect(names).toEqual(expect.arrayContaining(["rise-in", "pop", "stamp-in", "stamp-ripple", "shimmer", "pulse-dot"]));
  });

  it("nenhum @keyframes parte de opacity: 0", () => {
    const offenders: string[] = [];
    walk(tree, (b) => {
      if (!b.head.startsWith("@keyframes")) return;
      for (const frame of b.children) {
        if (!/^(from|0%)(\s*,\s*[\d.]+%)*$/.test(frame.head) && !/^0%/.test(frame.head)) continue;
        const text = declarations(frame).join(";");
        if (/opacity\s*:\s*0(\.0+)?\s*(;|$)/.test(text)) offenders.push(`${b.head} ${frame.head}`);
      }
    });
    expect(offenders).toEqual([]);
  });

  it("animacao com fill-mode both/backwards so existe sob [data-motion=\"on\"]", () => {
    const offenders: string[] = [];
    walk(tree, (b, parents) => {
      if (b.head === "" || b.head.startsWith("@")) return;
      if (parents.some((p) => p.head.startsWith("@keyframes") || p.head.startsWith("@media (prefers-reduced-motion"))) return;
      const anim = declarations(b).filter((d) => /^animation\s*:/.test(d));
      if (anim.some((d) => /\b(both|backwards)\b/.test(d)) && !b.head.split(",").every((s) => s.trim().startsWith('[data-motion="on"]'))) {
        offenders.push(b.head);
      }
    });
    expect(offenders).toEqual([]);
  });

  it("as animacoes fora do gate sao infinitas e decorativas", () => {
    const ungated: string[] = [];
    walk(tree, (b, parents) => {
      if (b.head === "" || b.head.startsWith("@")) return;
      if (parents.some((p) => p.head.startsWith("@keyframes") || p.head.startsWith("@media (prefers-reduced-motion"))) return;
      const anim = declarations(b).filter((d) => /^animation\s*:/.test(d));
      if (anim.length === 0) return;
      if (b.head.split(",").every((s) => s.trim().startsWith('[data-motion="on"]'))) return;
      ungated.push(`${b.head} => ${anim.join(" ")}`);
    });
    expect(ungated.length).toBeGreaterThan(0);
    for (const entry of ungated) expect(entry).toMatch(/infinite/);
    expect(ungated.map((e) => e.split(" =>")[0]!.trim()).sort()).toEqual([".pulse-dot", ".skeleton"]);
  });

  it("o estado base do carimbo e o desenho final (visivel sem animacao)", () => {
    const base = new Map<string, string>();
    walk(tree, (b, parents) => {
      if (b.head.startsWith(".stamp") && parents.every((p) => !p.head.startsWith("@") || p.head.startsWith("@layer"))) {
        base.set(b.head, declarations(b).join(";"));
      }
    });
    expect(base.get(".stamp-ring,\n  .stamp-check") ?? base.get(".stamp-ring, .stamp-check")).toMatch(/stroke-dashoffset\s*:\s*0/);
    expect(base.get(".stamp")).toMatch(/rotate\(-8deg\)/);
  });
});
