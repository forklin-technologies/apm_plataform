/**
 * Guarda de CSS contra a classe de defeito "conteudo invisivel porque a animacao nao rodou" (TASK-002,
 * rodada 1) e contra qualquer outro jeito de esconder conteudo no estado base ou no keyframe inicial
 * de uma animacao de entrada. Usado por src/app/motion-css.test.ts (CSS real e CSS de mutacao).
 */

export interface Block {
  head: string;
  body: string;
  children: Block[];
}

export function parseCss(css: string): Block[] {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, "");
  let i = 0;
  function readBlocks(): Block[] {
    const out: Block[] = [];
    let head = "";
    while (i < src.length) {
      const ch = src[i++]!;
      if (ch === "{") {
        out.push({ head: head.trim(), body: "", children: readBlocks() });
        head = "";
      } else if (ch === "}") {
        if (head.trim()) out.push({ head: "", body: head.trim(), children: [] });
        return out;
      } else if (ch === ";") {
        out.push({ head: "", body: head.trim(), children: [] });
        head = "";
      } else {
        head += ch;
      }
    }
    return out;
  }
  return readBlocks();
}

export function walk(blocks: Block[], visit: (b: Block, parents: Block[]) => void, parents: Block[] = []) {
  for (const b of blocks) {
    visit(b, parents);
    walk(b.children, visit, [...parents, b]);
  }
}

export const declarationsOf = (b: Block): Array<[string, string]> =>
  b.children
    .filter((c) => c.head === "" && c.body.includes(":"))
    .map((c) => {
      const idx = c.body.indexOf(":");
      return [c.body.slice(0, idx).trim().toLowerCase(), c.body.slice(idx + 1).trim().toLowerCase().replace(/\s*!important$/, "")] as [string, string];
    });

const toPx = (value: number, unit: string): number | null => {
  if (unit === "px" || unit === "") return value;
  if (unit === "rem" || unit === "em") return value * 16;
  return null;
};

/** O que, numa declaracao, esconde conteudo (devolve o motivo ou null). */
export function hidesContent(prop: string, value: string, where: "keyframe-start" | "base"): string | null {
  if (prop === "opacity") {
    const n = parseFloat(value);
    const v = value.endsWith("%") ? n / 100 : n;
    if (Number.isFinite(v) && v <= 0.05) return `opacity ${value}`;
  }
  if (prop === "visibility" && /^(hidden|collapse)$/.test(value)) return `visibility ${value}`;
  if (prop === "content-visibility" && value === "hidden") return "content-visibility hidden";
  if (prop === "clip-path" && value !== "none") return `clip-path ${value}`;
  if (prop === "clip" && /rect\(\s*0/.test(value)) return `clip ${value}`;
  if (prop === "transform" || prop === "scale") {
    // qualquer escala menor que 0,1 (inclui scale(0)) deixa o conteudo invisivel
    const groups = prop === "scale" ? [value] : [...value.matchAll(/scale(?:3d|x|y|z)?\(([^)]*)\)/g)].map((m) => m[1]!);
    for (const group of groups) {
      for (const part of group.split(/[\s,]+/).filter(Boolean)) {
        const n = parseFloat(part);
        if (Number.isFinite(n) && Math.abs(n) < 0.1) return `escala ${part} em ${prop}`;
      }
    }
  }
  if (prop === "transform" || prop === "translate") {
    const args = prop === "translate" ? [value] : [...value.matchAll(/translate(?:3d|x|y|z)?\(([^)]*)\)/g)].map((m) => m[1]!);
    for (const group of args) {
      for (const part of group.split(/[\s,]+/).filter(Boolean)) {
        const m = /^(-?[\d.]+)([a-z%]*)$/.exec(part);
        if (!m) continue;
        const n = parseFloat(m[1]!);
        const unit = m[2]!;
        if (unit === "%" && Math.abs(n) >= 50) return `translate ${part} (fora da tela)`;
        if (/^(vw|vh|vmin|vmax|dvh|dvw)$/.test(unit) && Math.abs(n) >= 50) return `translate ${part} (fora da tela)`;
        const px = toPx(n, unit);
        if (px !== null && Math.abs(px) > 32 && n !== 0) return `translate ${part} (fora da tela)`;
      }
    }
  }
  if (where === "keyframe-start" && /^(width|height|max-width|max-height)$/.test(prop) && /^0(\.0+)?(px|rem|em|%)?$/.test(value)) return `${prop} ${value}`;
  if (where === "keyframe-start" && prop === "display" && value === "none") return "display none";
  return null;
}

/** Seletores de base que escondem de proposito (decorativos / so leitor de tela). */
export const BASE_ALLOWLIST: Record<string, string> = {
  ".stamp-ripple": "ondulacao decorativa do carimbo: invisivel por padrao, so existe durante a animacao",
  ".sr-only-live": "texto so para leitor de tela",
  ".paper-grid::before": "textura decorativa do fundo (mascara)",
};

const isReducedMotionBlock = (b: Block) => b.head.startsWith("@media (prefers-reduced-motion");
const normalizeSelector = (s: string) => s.split(",").map((x) => x.trim().replace(/\s+/g, " ")).join(", ");

/** Problemas de "conteudo escondido" no CSS (lista vazia = ok). */
export function scanHiddenContent(css: string, allowlist: Record<string, string> = BASE_ALLOWLIST): string[] {
  const problems: string[] = [];
  walk(parseCss(css), (b, parents) => {
    // 1) primeiro quadro (from / 0%) de QUALQUER @keyframes
    if (b.head.startsWith("@keyframes")) {
      for (const frame of b.children) {
        if (!/^(from|0%)$/.test(frame.head.replace(/\s+/g, "")) && !/^0%/.test(frame.head.trim())) continue;
        for (const [prop, value] of declarationsOf(frame)) {
          const why = hidesContent(prop, value, "keyframe-start");
          if (why) problems.push(`@keyframes ${b.head.replace("@keyframes", "").trim()} (${frame.head}): ${why}`);
        }
      }
      return;
    }
    // 2) estado BASE: regras que nao estao dentro de keyframes
    if (b.head === "" || b.head.startsWith("@")) return;
    if (parents.some((p) => p.head.startsWith("@keyframes") || isReducedMotionBlock(p))) return;
    const selector = normalizeSelector(b.head);
    if (selector.split(", ").every((s) => s in allowlist)) return;
    for (const [prop, value] of declarationsOf(b)) {
      const why = hidesContent(prop, value, "base");
      if (why) problems.push(`${selector}: ${why}`);
    }
  });
  return problems;
}

/** Animacoes com fill-mode both/backwards fora do gate [data-motion="on"], e TODAS as animacoes fora dele (o teste exige que sejam infinitas). */
export function scanUngatedAnimations(css: string): { fillOutsideGate: string[]; ungated: string[] } {
  const fillOutsideGate: string[] = [];
  const ungated: string[] = [];
  walk(parseCss(css), (b, parents) => {
    if (b.head === "" || b.head.startsWith("@")) return;
    if (parents.some((p) => p.head.startsWith("@keyframes") || isReducedMotionBlock(p))) return;
    const anims = declarationsOf(b).filter(([prop]) => prop === "animation" || prop === "animation-fill-mode");
    if (anims.length === 0) return;
    const gated = b.head.split(",").every((s) => s.trim().startsWith('[data-motion="on"]'));
    const text = anims.map(([p, v]) => `${p}: ${v}`).join("; ");
    if (!gated && /\b(both|backwards)\b/.test(text)) fillOutsideGate.push(`${normalizeSelector(b.head)} => ${text}`);
    if (!gated) ungated.push(`${normalizeSelector(b.head)} => ${text}`);
  });
  return { fillOutsideGate, ungated };
}
